"""Owner review of member submissions: DM with buttons, then Notion on approval.

Drafts come from the forum listeners (``forums.py``) and ``/demande``
(``requests.py``). The owner gets one message per draft with persistent
buttons — the custom ids carry the draft id, so they keep working after a
restart — and only an approval creates the page in the (public) Œuvres base.
"""

import asyncio
import os
import re

from interactions import (
    ActionRow,
    AllowedMentions,
    Button,
    ButtonStyle,
    ComponentContext,
    Embed,
    Modal,
    ModalContext,
    Permissions,
    ShortText,
    component_callback,
    modal_callback,
    spread_to_rows,
)

from features.confrerie import notion_props as np
from features.confrerie.drafts import (
    SOURCE_DEFI,
    SOURCE_DEMANDE,
    SOURCE_TEXTES,
    STATUS_APPROVED,
    STATUS_DONE,
    STATUS_PENDING,
    STATUS_REJECTED,
    Draft,
    build_work_properties,
    split_list,
)
from features.confrerie.repository import DraftRepository
from features.confrerie.stats import COL_AUTHOR, COL_DEFI, COL_GENRE, COL_TYPE
from src.core import logging as logutil
from src.core.errors import BotError
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import fetch_user_safe

from ._common import config, guild_id, module_config

logger = logutil.init_logger(__name__)

_SOURCE_LABELS = {
    SOURCE_TEXTES: "Forum « textes »",
    SOURCE_DEFI: "Forum « défis »",
    SOURCE_DEMANDE: "/demande",
}
_STATUS_LINES = {
    STATUS_APPROVED: "✅ Fiche créée par {who}",
    STATUS_DONE: "✔️ Marquée comme traitée par {who}",
    STATUS_REJECTED: "✖️ Ignorée par {who}",
}


def review_embed(draft: Draft, status_line: str = "") -> Embed:
    """What the owner sees for one draft (and, once handled, its outcome)."""
    title = "📝 Nouvelle demande" if draft.source == SOURCE_DEMANDE else "📝 Fiche à valider"
    embed = Embed(title=title, color=Colors.CONFRERIE, timestamp=draft.created_at)
    embed.add_field(name="Titre", value=draft.title[:1024], inline=False)
    authors = ", ".join(draft.authors) or "—"
    if draft.unmapped_author:
        authors += (
            "\n⚠️ Membre non associé à un nom d'auteur·ice "
            "(config « Auteur·ices ») : pseudo Discord proposé."
        )
    embed.add_field(name="Auteur·ice", value=authors[:1024], inline=True)
    embed.add_field(name="Soumis par", value=f"<@{draft.submitter_id}>", inline=True)
    embed.add_field(
        name="Source", value=_SOURCE_LABELS.get(draft.source, draft.source), inline=True
    )
    if draft.types:
        embed.add_field(name="Type", value=", ".join(draft.types), inline=True)
    if draft.genres:
        embed.add_field(name="Genre", value=", ".join(draft.genres), inline=True)
    if draft.defi:
        embed.add_field(name="Défi", value=draft.defi, inline=True)
    elif draft.source == SOURCE_DEFI:
        embed.add_field(name="Défi", value="⚠️ Non reconnu — à préciser via Modifier", inline=True)
    if draft.link:
        embed.add_field(name=draft.link_label, value=draft.link[:1024], inline=False)
    if draft.details:
        embed.add_field(name="Détails", value=draft.details[:1024], inline=False)
    if draft.notion_url:
        embed.add_field(name="Notion", value=f"[Voir la fiche]({draft.notion_url})", inline=False)
    if status_line:
        embed.add_field(name="Statut", value=status_line, inline=False)
    return embed


def review_buttons(draft: Draft, *, disabled: bool = False) -> list[ActionRow]:
    is_demande = draft.source == SOURCE_DEMANDE
    buttons = [
        Button(
            style=ButtonStyle.SUCCESS,
            label="Créer la fiche" if is_demande else "Valider",
            emoji="✅",
            custom_id=f"confrerie_draft:approve:{draft.id}",
            disabled=disabled,
        ),
        Button(
            style=ButtonStyle.PRIMARY,
            label="Modifier et valider",
            emoji="✏️",
            custom_id=f"confrerie_draft:edit:{draft.id}",
            disabled=disabled,
        ),
    ]
    if is_demande:
        buttons.append(
            Button(
                style=ButtonStyle.SECONDARY,
                label="Traité (sans fiche)",
                emoji="✔️",
                custom_id=f"confrerie_draft:done:{draft.id}",
                disabled=disabled,
            )
        )
    buttons.append(
        Button(
            style=ButtonStyle.DANGER,
            label="Refuser" if is_demande else "Ignorer",
            custom_id=f"confrerie_draft:reject:{draft.id}",
            disabled=disabled,
        )
    )
    return spread_to_rows(*buttons)


def _prefilled(label: str, custom_id: str, value: str, *, required: bool = False) -> ShortText:
    """Short text input pre-filled with ``value`` (omitted when empty: Discord rejects null)."""
    kwargs = {"value": value[:200]} if value else {}
    return ShortText(label=label, custom_id=custom_id, required=required, max_length=200, **kwargs)


class ReviewMixin:
    """Store drafts, send them for review, and act on the owner's decision."""

    def _drafts(self) -> DraftRepository:
        if not guild_id:
            raise BotError("moduleConfrerie n'est activé sur aucun serveur")
        return DraftRepository(guild_id)

    async def _oeuvres_schema(self, timeout: float | None = None) -> dict:
        """Live Œuvres schema, or {} when unavailable (option matching is then skipped).

        Pass ``timeout`` on paths that must answer an interaction within
        Discord's 3 s window.
        """
        db_id = module_config.get("confrerieNotionDbOeuvresId")
        if not db_id:
            return {}
        try:
            return await asyncio.wait_for(
                self.notion_client.get_properties_schema(db_id), timeout=timeout
            )
        except Exception as e:  # noqa: BLE001 — drafts still work without option matching
            logger.warning("Schéma Notion des œuvres indisponible: %s", e)
            return {}

    async def submit_draft(self, draft: Draft) -> bool:
        """Store ``draft`` and send it to the owner.

        Returns False when an identical draft already exists (same forum
        thread / same member in a défi thread) or nobody could be notified —
        in which case the draft is discarded.
        """
        repo = self._drafts()
        if not await repo.insert_if_new(draft):
            logger.debug("Brouillon %s déjà connu, ignoré", draft.id)
            return False
        message = await self._send_for_review(draft)
        if message is None:
            # Nobody will ever see it: drop it so the next attempt isn't deduped away.
            logger.error("Brouillon %s abandonné : personne n'a pu être prévenu", draft.id)
            await repo.delete(draft.id)
            return False
        draft.review_channel_id = int(message.channel.id)
        draft.review_message_id = int(message.id)
        await repo.save(draft)
        logger.info("Brouillon %s (%s) envoyé pour validation", draft.id, draft.title)
        return True

    async def _send_for_review(self, draft: Draft):
        """DM the confrérie owner; fall back to the staff channel, then the bot owner."""
        embed = review_embed(draft)
        components = review_buttons(draft)

        owner_id = module_config.get("confrerieOwnerId")
        if owner_id:
            _, user = await fetch_user_safe(self.bot, owner_id)
            if user:
                try:
                    return await user.send(embed=embed, components=components)
                except Exception as e:  # noqa: BLE001 — DMs closed: use the fallback
                    logger.warning("MP au propriétaire impossible: %s", e)

        staff_id = module_config.get("confrerieStaffChannelId")
        if staff_id:
            try:
                channel = await self.bot.fetch_channel(staff_id)
                if channel and hasattr(channel, "send"):
                    return await channel.send(embed=embed, components=components)
            except Exception as e:  # noqa: BLE001
                logger.warning("Salon de validation inaccessible: %s", e)

        bot_owner_id = config.get("discord", {}).get("ownerId")
        if bot_owner_id and bot_owner_id != owner_id:
            _, user = await fetch_user_safe(self.bot, bot_owner_id)
            if user:
                try:
                    return await user.send(embed=embed, components=components)
                except Exception as e:  # noqa: BLE001
                    logger.warning("MP au propriétaire du bot impossible: %s", e)
        return None

    def _can_review(self, ctx) -> bool:
        reviewers = {
            str(module_config.get("confrerieOwnerId") or ""),
            str(config.get("discord", {}).get("ownerId") or ""),
        } - {""}
        if str(ctx.author.id) in reviewers:
            return True
        return bool(ctx.guild) and ctx.author.has_permission(Permissions.MANAGE_GUILD)

    # ── Buttons ──────────────────────────────────────────────────────────

    @component_callback(re.compile(r"confrerie_draft:(approve|edit|done|reject):(.+)"))
    async def on_draft_button(self, ctx: ComponentContext):
        _, action, draft_id = ctx.custom_id.split(":", 2)
        if not self._can_review(ctx):
            await ctx.send(
                "Seul·e le/la propriétaire de la confrérie peut valider.", ephemeral=True
            )
            return

        repo = self._drafts()
        if action == "edit":
            draft = await repo.get(draft_id)
            if draft is None or draft.status != STATUS_PENDING:
                await ctx.send("Cette fiche a déjà été traitée.", ephemeral=True)
                return
            await ctx.send_modal(self._edit_modal(draft))
            return

        draft = await repo.claim(draft_id)
        if draft is None:
            await ctx.send("Cette fiche a déjà été traitée.", ephemeral=True)
            return

        if action == "approve":
            await ctx.defer(edit_origin=True)
            if not await self._approve(draft, repo):
                await ctx.send(
                    "Notion a refusé la création de la fiche, réessayez.", ephemeral=True
                )
                return
            status = STATUS_APPROVED
        else:
            status = STATUS_DONE if action == "done" else STATUS_REJECTED
            await repo.set_status(draft.id, status)
            draft.status = status

        who = ctx.author.mention
        await ctx.edit_origin(
            embed=review_embed(draft, _STATUS_LINES[status].format(who=who)),
            components=review_buttons(draft, disabled=True),
        )
        await self._notify_submitter(draft)

    # ── "Modifier et valider" modal ─────────────────────────────────────

    def _edit_modal(self, draft: Draft) -> Modal:
        return Modal(
            _prefilled("Titre", "titre", draft.title, required=True),
            _prefilled(
                "Auteur·ices (séparés par des virgules)", "auteurs", ", ".join(draft.authors)
            ),
            _prefilled("Type(s)", "types", ", ".join(draft.types)),
            _prefilled("Genre(s)", "genres", ", ".join(draft.genres)),
            _prefilled("Défi", "defi", draft.defi or ""),
            title="Modifier la fiche",
            custom_id=f"confrerie_draft_edit:{draft.id}",
        )

    @modal_callback(re.compile(r"confrerie_draft_edit:(.+)"))
    async def on_draft_edit(self, ctx: ModalContext, **responses: str):
        draft_id = ctx.custom_id.split(":", 1)[1]
        if not self._can_review(ctx):
            await ctx.send(
                "Seul·e le/la propriétaire de la confrérie peut valider.", ephemeral=True
            )
            return
        repo = self._drafts()
        draft = await repo.claim(draft_id)
        if draft is None:
            await ctx.send("Cette fiche a déjà été traitée.", ephemeral=True)
            return

        schema = await self._oeuvres_schema()
        # The owner may introduce a new author or défi: canonicalise the case of
        # known options, keep the rest as typed.
        draft.title = (responses.get("titre") or draft.title).strip()[:100]
        draft.authors = np.canonical_choices(
            split_list(responses.get("auteurs", "")),
            np.schema_options(schema, COL_AUTHOR),
            "Auteur",
            strict=False,
        )
        draft.types = np.canonical_choices(
            split_list(responses.get("types", "")),
            np.schema_options(schema, COL_TYPE),
            "Type",
            strict=False,
        )
        draft.genres = np.canonical_choices(
            split_list(responses.get("genres", "")),
            np.schema_options(schema, COL_GENRE),
            "Genre",
            strict=False,
        )
        defi = np.canonical_choices(
            [responses.get("defi", "")], np.schema_options(schema, COL_DEFI), "Défi", strict=False
        )
        draft.defi = defi[0] if defi else None
        draft.unmapped_author = False

        await ctx.defer(ephemeral=True)
        if not await self._approve(draft, repo):
            await ctx.send("Notion a refusé la création de la fiche, réessayez.", ephemeral=True)
            return
        await self._refresh_review_message(
            draft, _STATUS_LINES[STATUS_APPROVED].format(who=ctx.author.mention)
        )
        await ctx.send("✅ Fiche créée.", ephemeral=True)
        await self._notify_submitter(draft)

    # ── Outcome ──────────────────────────────────────────────────────────

    async def _approve(self, draft: Draft, repo: DraftRepository) -> bool:
        """Create the Notion page for a claimed draft; release it on failure."""
        db_id = module_config.get("confrerieNotionDbOeuvresId")
        try:
            if not db_id:
                raise BotError("Base Notion des œuvres non configurée")
            properties, dropped = np.filter_to_schema(
                build_work_properties(draft), await self._oeuvres_schema()
            )
            if dropped:
                logger.warning("Colonnes absentes de la base Œuvres, ignorées : %s", dropped)
            page = await self.notion_client.create_page(database_id=db_id, properties=properties)
        except Exception as e:  # noqa: BLE001 — put the draft back up for review
            logger.error("Création de la fiche %s impossible: %s", draft.id, e)
            await repo.set_status(draft.id, STATUS_PENDING)
            return False
        draft.status = STATUS_APPROVED
        draft.notion_url = np.page_url(page)
        await repo.save(draft)
        logger.info("Fiche Notion créée pour le brouillon %s", draft.id)
        return True

    async def _refresh_review_message(self, draft: Draft, status_line: str) -> None:
        if not (draft.review_channel_id and draft.review_message_id):
            return
        try:
            channel = await self.bot.fetch_channel(draft.review_channel_id)
            message = await channel.fetch_message(draft.review_message_id)
            await message.edit(
                embed=review_embed(draft, status_line),
                components=review_buttons(draft, disabled=True),
            )
        except Exception as e:  # noqa: BLE001 — cosmetic, the draft is already handled
            logger.warning("Message de validation %s non mis à jour: %s", draft.id, e)

    async def _notify_submitter(self, draft: Draft) -> None:
        """Tell the member what became of their submission."""
        try:
            if draft.source == SOURCE_DEMANDE:
                _, user = await fetch_user_safe(self.bot, draft.submitter_id)
                if user is None:
                    return
                if draft.status == STATUS_APPROVED:
                    text = (
                        f"✅ Votre texte « {draft.title} » a été ajouté à la base de la confrérie"
                    )
                    text += f" : {draft.notion_url}" if draft.notion_url else "."
                elif draft.status == STATUS_DONE:
                    text = f"✅ Votre demande « {draft.title} » a été traitée."
                else:
                    text = f"Votre demande « {draft.title} » n'a pas été retenue."
                await user.send(text)
            elif draft.status == STATUS_APPROVED and draft.channel_id:
                channel = await self.bot.fetch_channel(draft.channel_id)
                if channel and hasattr(channel, "send"):
                    link = f" : {draft.notion_url}" if draft.notion_url else "."
                    await channel.send(
                        f"📚 « {draft.title} » a été ajouté à la base de la confrérie{link}",
                        reply_to=draft.message_id,
                        # The title comes from a member: never let it ping @everyone.
                        allowed_mentions=AllowedMentions(parse=[], replied_user=True),
                    )
        except Exception as e:  # noqa: BLE001 — the decision stands even if the notice fails
            logger.warning("Notification du brouillon %s impossible: %s", draft.id, e)
