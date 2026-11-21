"""``/suggerer`` (anyone), the staff decisions, and the card's buttons.

``/suggerer`` opens a form (a title and the idea); right-click a message →
Applications → "💡 En faire une suggestion" opens the same form holding the
message's text, and the suggestion links back to it. Each suggestion is posted
with 👍 / 👎 and the staff's ✅ Approuver / ❌ Refuser / 🛠️ Implémentée: anyone
sees them, only the staff role (or an administrator) may use them, and each asks
for an optional reason. A "Discussion #12" thread opens on the post. The author
hears of the decision by DM. ``/suggestion approuver|refuser|implementer`` does
the same from a command, its ``id`` autocompleting the open suggestions.
"""

import re
from typing import TYPE_CHECKING, Any

from interactions import (
    ActionRow,
    AutocompleteContext,
    Button,
    ButtonStyle,
    ComponentContext,
    ContextMenuContext,
    Embed,
    Modal,
    ModalContext,
    OptionType,
    ParagraphText,
    Permissions,
    ShortText,
    SlashContext,
    component_callback,
    message_context_menu,
    modal_callback,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.suggestions import DECISIONS_FROM, Suggestion, next_decisions
from src.core import clock
from src.core.logging import log_failure
from src.core.text import clip
from src.discord_ext.autocomplete import CHOICE_NAME_LIMIT, MAX_CHOICES
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import card
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.menus import menu_help
from src.discord_ext.messages import (
    edit_message_if_changed,
    fetch_user_safe,
    guild_id_of,
    jump_url,
    module_off,
    require_guild,
    send_error,
    send_success,
)
from src.discord_ext.scopes import guild_scopes
from src.discord_ext.staff import is_staff

from ._common import (
    STATUS_KINDS,
    STATUS_LABELS,
    SUGGESTIONS,
    VOTE_PREFIX,
    get_guild_settings,
    logger,
)

MAX_SUGGESTION_LENGTH = 1500
MAX_TITLE_LENGTH = 100
MAX_REASON_LENGTH = 500
SUGGEST_MENU = "💡 En faire une suggestion"
NEW_FORM = "suggest_new"
SUGGEST_FORM = "suggest_from"  # suggest_from:{channel_id}:{message_id}
_SUGGEST_FORM_RE = re.compile(rf"^{SUGGEST_FORM}:(\d+):(\d+)$")
DECIDE_PREFIX = "suggdecide"  # suggdecide:{sugg_id}:{status}
REASON_PREFIX = "suggreason"  # suggreason:{sugg_id}:{status}
_STATUSES = "|".join(DECISIONS_FROM)
_DECIDE_RE = re.compile(rf"^{DECIDE_PREFIX}:(\d+):({_STATUSES})$")
_REASON_RE = re.compile(rf"^{REASON_PREFIX}:(\d+):({_STATUSES})$")

# The staff's buttons, by the decision they take.
DECISION_BUTTONS: dict[str, tuple[str, str]] = {
    "approved": ("Approuver", "✅"),
    "denied": ("Refuser", "❌"),
    "implemented": ("Implémentée", "🛠️"),
}
# What the reason form asks, by decision.
REASON_TITLES = {
    "approved": "Approuver la suggestion",
    "denied": "Refuser la suggestion",
    "implemented": "Marquer comme implémentée",
}
# A decision that closes the discussion: its thread is archived.
FINAL = ("denied", "implemented")


def _build_embed(suggestion: Suggestion, *, anonymous: bool) -> Embed:
    up, down = suggestion.tally()
    author_value = "Anonyme" if anonymous else f"<@{suggestion.author_id}>"
    title = f"Suggestion #{suggestion.id}"
    if suggestion.title:
        title += f" · {suggestion.title}"

    embed = card(
        "moduleSuggestions",
        STATUS_KINDS.get(suggestion.status, "info"),
        title=title,
        description=suggestion.text,
    )
    embed.add_field(name="Statut", value=STATUS_LABELS[suggestion.status], inline=True)
    embed.add_field(name="Auteur", value=author_value, inline=True)
    embed.add_field(name="Votes", value=f"👍 {up} · 👎 {down}", inline=True)
    if suggestion.source_url:
        embed.add_field(
            name="D'après", value=f"[ce message]({suggestion.source_url})", inline=False
        )

    if suggestion.status != "pending" and (suggestion.reason or suggestion.decided_by):
        details = []
        if suggestion.decided_by:
            details.append(f"Décision : <@{suggestion.decided_by}>")
        if suggestion.reason:
            details.append(f"Motif : {suggestion.reason}")
        embed.add_field(name="​", value="\n".join(details), inline=False)

    embed.timestamp = suggestion.created_at
    return embed


def staff_only(staff_role: Any) -> str:
    """Who may decide, said to a member who may not."""
    if staff_role:
        return (
            f"Seuls le rôle <@&{staff_role}> et les administrateurs peuvent trancher "
            "une suggestion."
        )
    return (
        "Seuls les administrateurs peuvent trancher une suggestion (aucun rôle staff "
        "n'est choisi dans le tableau de bord)."
    )


def suggest_form(custom_id: str, text: str = "") -> Modal:
    """The form of ``/suggerer`` and of the right-click menu (then holding the message)."""
    return Modal(
        ShortText(
            label="En quelques mots",
            custom_id="title",
            placeholder="Ex. Un salon pour la musique",
            required=True,
            max_length=MAX_TITLE_LENGTH,
        ),
        ParagraphText(
            label="Ta suggestion",
            custom_id="text",
            placeholder="Explique ton idée : ce qu'elle change, pourquoi elle aiderait",
            value=text[:MAX_SUGGESTION_LENGTH] or None,
            required=True,
            max_length=MAX_SUGGESTION_LENGTH,
        ),
        title="Proposer une suggestion",
        custom_id=custom_id,
    )


def reason_form(sugg_id: int, status: str) -> Modal:
    return Modal(
        ParagraphText(
            label="Motif (facultatif)" if status != "implemented" else "Note (facultatif)",
            custom_id="reason",
            placeholder="L'auteur le reçoit en MP, et il s'affiche sur la suggestion",
            required=False,
            max_length=MAX_REASON_LENGTH,
        ),
        title=f"{REASON_TITLES[status]} #{sugg_id}",
        custom_id=f"{REASON_PREFIX}:{sugg_id}:{status}",
    )


def suggestion_components(suggestion: Suggestion) -> list[ActionRow]:
    """👍 / 👎 while the vote is open, and the staff decisions still possible."""
    sugg_id, pending = suggestion.id, suggestion.status == "pending"
    rows = [
        ActionRow(
            Button(
                label="Pour",
                style=ButtonStyle.SUCCESS,
                emoji="👍",
                custom_id=f"{VOTE_PREFIX}:{sugg_id}:up",
                disabled=not pending,
            ),
            Button(
                label="Contre",
                style=ButtonStyle.DANGER,
                emoji="👎",
                custom_id=f"{VOTE_PREFIX}:{sugg_id}:down",
                disabled=not pending,
            ),
        )
    ]
    decisions = next_decisions(suggestion.status)
    if decisions:
        rows.append(
            ActionRow(
                *(
                    Button(
                        label=DECISION_BUTTONS[status][0],
                        emoji=DECISION_BUTTONS[status][1],
                        style=ButtonStyle.SECONDARY,
                        custom_id=f"{DECIDE_PREFIX}:{sugg_id}:{status}",
                    )
                    for status in decisions
                )
            )
        )
    return rows


def decision_text(suggestion: Suggestion, guild_name: str, link: str | None) -> str:
    """The DM its author gets: what was decided, why, and where."""
    label = STATUS_LABELS[suggestion.status].split(" ", 1)[1].lower()
    title = f" « {suggestion.title} »" if suggestion.title else ""
    lines = [f"💡 Ta suggestion **#{suggestion.id}**{title} sur **{guild_name}** est {label}."]
    if suggestion.reason:
        lines.append(f"Motif : {suggestion.reason}")
    if link:
        lines.append(f"🔗 {link}")
    return "\n".join(lines)


def open_choices(suggestions: list[Suggestion], typed: str) -> list[dict[str, Any]]:
    """The staff autocomplete: "#12 · Un salon musique", matched on number or words."""
    needle = typed.strip().lower().lstrip("#")
    choices = []
    for s in suggestions:
        label = f"#{s.id} · {s.title or s.text}"
        if needle in label.lower():
            choices.append({"name": clip(label, CHOICE_NAME_LIMIT), "value": s.id})
    return choices[:MAX_CHOICES]


def _id_option(description: str = "La suggestion (choisis-la dans la liste)"):
    return slash_option(
        "id",
        description,
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=1,
        autocomplete=True,
        argument_name="sugg_id",
    )


def _reason_option(description: str = "Motif, envoyé à l'auteur (facultatif)"):
    return slash_option(
        "motif",
        description,
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_REASON_LENGTH,
        argument_name="reason",
    )


class CommandsMixin:
    """``/suggerer``, the form, the staff's buttons and ``/suggestion ...``."""

    bot: Any

    if TYPE_CHECKING:

        def repository(self, guild_id: str | int) -> Any: ...

    @slash_command(
        name="suggerer",
        description="Proposer une idée pour le serveur",
        scopes=guild_scopes(SUGGESTIONS.guild_ids()),
    )
    async def suggest(self, ctx: SlashContext) -> None:
        if not await require_guild(ctx):
            return
        if not get_guild_settings(ctx.guild_id):
            await send_error(ctx, module_off("suggestions"), icon="💡")
            return
        await ctx.send_modal(suggest_form(NEW_FORM))

    @modal_callback(NEW_FORM)
    async def on_new_form(self, ctx: ModalContext, title: str = "", text: str = "") -> None:
        await self.publish_suggestion(ctx, text.strip(), title=title.strip())

    @menu_help("Proposer ce message comme suggestion, à reformuler")
    @message_context_menu(name=SUGGEST_MENU, scopes=guild_scopes(SUGGESTIONS.guild_ids()))
    async def suggest_from_message(self, ctx: ContextMenuContext) -> None:
        message: Any = ctx.target
        custom_id = f"{SUGGEST_FORM}:{ctx.channel_id}:{message.id}"
        await ctx.send_modal(suggest_form(custom_id, message.content or ""))

    @modal_callback(_SUGGEST_FORM_RE)
    async def on_suggest_form(self, ctx: ModalContext, title: str = "", text: str = "") -> None:
        match = _SUGGEST_FORM_RE.match(ctx.custom_id)
        if not match:
            return
        channel_id, message_id = match.groups()
        source = jump_url(guild_id_of(ctx), channel_id, message_id)
        await self.publish_suggestion(ctx, text.strip(), title=title.strip(), source_url=source)

    async def publish_suggestion(
        self, ctx: Any, text: str, *, title: str = "", source_url: str | None = None
    ) -> None:
        """Post *text* as a suggestion in the configured channel, and open its thread."""
        settings = get_guild_settings(ctx.guild_id)
        if not settings:
            await send_error(ctx, module_off("suggestions"), icon="💡")
            return
        if not text:
            await send_error(ctx, "Ta suggestion est vide.")
            return
        if len(text) > MAX_SUGGESTION_LENGTH:
            await send_error(
                ctx,
                f"La suggestion est trop longue ({len(text)} caractères) : "
                f"{MAX_SUGGESTION_LENGTH} au plus.",
            )
            return

        channel_id = settings.get("suggestChannelId")
        if not channel_id:
            await send_error(
                ctx,
                "Aucun salon de suggestions n'est choisi : un administrateur peut le faire "
                "dans le tableau de bord.",
            )
            return

        channel = await fetch_guild_channel(self.bot, guild_id_of(ctx), channel_id, logger=logger)
        if channel is None or not hasattr(channel, "send"):
            await send_error(ctx, "Salon de suggestions introuvable.")
            return

        await ctx.defer(ephemeral=True)

        repo = self.repository(ctx.guild_id)
        sugg_id = await repo.next_id()
        suggestion = Suggestion(
            id=sugg_id,
            guild_id=str(ctx.guild_id),
            channel_id=str(channel.id),
            author_id=str(ctx.author.id),
            title=title[:MAX_TITLE_LENGTH] or None,
            text=text,
            source_url=source_url,
            created_at=clock.now(),
        )
        await repo.add(suggestion)

        anonymous = bool(settings.get("anonymous", False))
        try:
            sent = await channel.send(  # type: ignore[union-attr]
                embeds=[_build_embed(suggestion, anonymous=anonymous)],
                components=suggestion_components(suggestion),
            )
        except Exception as e:
            log_failure(logger, "Failed to publish suggestion %s: %s", sugg_id, e)
            await send_error(ctx, "Je n'ai pas pu publier la suggestion : réessaie.")
            return

        await repo.set_message(sugg_id, str(sent.id))
        if settings.get("discussionThreads", True):
            await self._open_thread(repo, suggestion, sent)
        logger.info(
            "Suggestion #%s created by %s in guild %s", sugg_id, ctx.author.username, ctx.guild_id
        )
        await send_success(ctx, f"Suggestion **#{sugg_id}** publiée dans <#{channel.id}>.")

    async def _open_thread(self, repo: Any, suggestion: Suggestion, message: Any) -> None:
        """The "Discussion #12" thread on the post; the suggestion stands without it."""
        name = f"Discussion #{suggestion.id}"
        if suggestion.title:
            name += f" · {suggestion.title}"
        try:
            thread = await message.create_thread(name=clip(name, 100))
            await repo.set_thread(suggestion.id, str(thread.id))
        except Exception as e:
            logger.warning("No discussion thread for suggestion #%s: %s", suggestion.id, e)

    # ── The staff's buttons ─────────────────────────────────────────────

    @component_callback(_DECIDE_RE)
    async def on_decide_button(self, ctx: ComponentContext) -> None:
        match = _DECIDE_RE.match(ctx.custom_id)
        if not match:
            return
        settings = get_guild_settings(ctx.guild_id) if ctx.guild_id else None
        if not settings:
            await send_error(ctx, module_off("suggestions"), icon="💡")
            return
        staff_role = settings.get("staffRoleId")
        if not is_staff(ctx.author, staff_role):
            await send_error(ctx, staff_only(staff_role), icon="🔒")
            return
        await ctx.send_modal(reason_form(int(match[1]), match[2]))

    @modal_callback(_REASON_RE)
    async def on_reason_form(self, ctx: ModalContext, reason: str = "") -> None:
        match = _REASON_RE.match(ctx.custom_id)
        if not match:
            return
        await self.decide(ctx, int(match[1]), match[2], reason.strip() or None)

    # ── /suggestion approuver | refuser | implementer ──────────────────

    @slash_command(
        name="suggestion",
        description="Trancher une suggestion (staff)",
        sub_cmd_name="approuver",
        sub_cmd_description="Approuver une suggestion",
        scopes=guild_scopes(SUGGESTIONS.guild_ids()),
    )
    @_id_option()
    @_reason_option()
    # Hidden from members (B9); the staff role is checked when it runs. A staff
    # role without "Gérer les messages" gets it back in the server's
    # Intégrations settings (the schema says so).
    @slash_default_member_permission(Permissions.MANAGE_MESSAGES)
    async def suggestion_approve(
        self, ctx: SlashContext, sugg_id: int, reason: str | None = None
    ) -> None:
        await self.decide(ctx, sugg_id, "approved", reason)

    @suggestion_approve.subcommand(
        sub_cmd_name="refuser",
        sub_cmd_description="Refuser une suggestion",
    )
    @_id_option()
    @_reason_option()
    async def suggestion_deny(
        self, ctx: SlashContext, sugg_id: int, reason: str | None = None
    ) -> None:
        await self.decide(ctx, sugg_id, "denied", reason)

    @suggestion_approve.subcommand(
        sub_cmd_name="implementer",
        sub_cmd_description="Marquer une suggestion comme implémentée",
    )
    @_id_option()
    @_reason_option("Note, envoyée à l'auteur (facultatif)")
    async def suggestion_implement(
        self, ctx: SlashContext, sugg_id: int, reason: str | None = None
    ) -> None:
        await self.decide(ctx, sugg_id, "implemented", reason)

    @suggestion_implement.autocomplete("id")
    @suggestion_deny.autocomplete("id")
    @suggestion_approve.autocomplete("id")
    async def open_suggestion_choices(self, ctx: AutocompleteContext) -> None:
        """The suggestions still to decide, newest first (approved ones, to implement them)."""
        if ctx.guild_id is None or not get_guild_settings(ctx.guild_id):
            await ctx.send(choices=[])
            return
        try:
            found = await self.repository(ctx.guild_id).list_in(["pending", "approved"], 100)
        except Exception as e:
            logger.warning("Could not list the open suggestions: %s", e)
            found = []
        await ctx.send(choices=open_choices(found, ctx.input_text))

    async def decide(self, ctx: Any, sugg_id: int, status: str, reason: str | None) -> None:
        """Take a staff decision, from a button's form or a command, once."""
        if not await require_guild(ctx):
            return
        settings = get_guild_settings(ctx.guild_id)
        if not settings:
            await send_error(ctx, module_off("suggestions"), icon="💡")
            return
        staff_role = settings.get("staffRoleId")
        if not is_staff(ctx.author, staff_role):
            await send_error(ctx, staff_only(staff_role), icon="🔒")
            return

        repo = self.repository(ctx.guild_id)
        suggestion = await repo.get(sugg_id)
        if not suggestion:
            await send_error(ctx, f"Suggestion #{sugg_id} introuvable.")
            return
        decided = await repo.decide(
            sugg_id,
            status,
            reason,
            str(ctx.author.id),
            from_statuses=DECISIONS_FROM[status],  # type: ignore[index]
        )
        if decided is None:
            current = (await repo.get(sugg_id)) or suggestion
            await send_error(
                ctx,
                f"La suggestion **#{sugg_id}** est déjà {STATUS_LABELS[current.status]} : "
                "rien n'a changé.",
            )
            return

        await self._show_decision(ctx, decided, settings)
        logger.info(
            "Suggestion #%s -> %s by %s (guild %s)",
            sugg_id,
            status,
            ctx.author.username,
            ctx.guild_id,
        )
        await send_success(ctx, f"Suggestion **#{sugg_id}** {STATUS_LABELS[status]}.")

    async def _show_decision(self, ctx: Any, suggestion: Suggestion, settings: dict) -> None:
        """Update the card, tell the thread and the author. Each step stands alone."""
        guild_id = guild_id_of(ctx)
        anonymous = bool(settings.get("anonymous", False))
        link = (
            jump_url(guild_id, suggestion.channel_id, suggestion.message_id)
            if suggestion.message_id
            else None
        )
        channel = await fetch_guild_channel(self.bot, guild_id, suggestion.channel_id)
        if suggestion.message_id and channel is not None and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(int(suggestion.message_id))
                await edit_message_if_changed(
                    message,
                    embeds=[_build_embed(suggestion, anonymous=anonymous)],
                    components=suggestion_components(suggestion),
                    logger=logger,
                )
            except Exception as e:
                logger.warning("Could not update suggestion #%s message: %s", suggestion.id, e)
        if suggestion.thread_id:
            await self._tell_thread(ctx, guild_id, suggestion)
        if suggestion.author_id != str(ctx.author.id):
            guild_name = getattr(ctx.guild, "name", None) or "le serveur"
            try:
                _, author = await fetch_user_safe(self.bot, suggestion.author_id)
                if author is not None:
                    await author.send(decision_text(suggestion, guild_name, link))
            except Exception as e:
                logger.info("Suggestion #%s author not told (DMs closed?): %s", suggestion.id, e)

    async def _tell_thread(self, ctx: Any, guild_id: str, suggestion: Suggestion) -> None:
        thread = await fetch_guild_channel(self.bot, guild_id, suggestion.thread_id)
        if thread is None or not hasattr(thread, "send"):
            return
        line = f"{STATUS_LABELS[suggestion.status]} par {ctx.author.display_name}"
        if suggestion.reason:
            line += f" : {suggestion.reason}"
        try:
            await thread.send(line, allowed_mentions=NO_PINGS)  # type: ignore[union-attr]
            if suggestion.status in FINAL and hasattr(thread, "edit"):
                await thread.edit(archived=True, locked=True)  # type: ignore[union-attr]
        except Exception as e:
            logger.warning("Could not post in suggestion #%s thread: %s", suggestion.id, e)
