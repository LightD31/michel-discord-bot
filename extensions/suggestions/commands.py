"""Slash commands: ``/suggest`` (anyone) and ``/suggestion approve|deny|implement`` (staff).

Right-click a message → Applications → "💡 En faire une suggestion" opens a
form holding the message's text, which the member rewrites as they like; the
suggestion links back to the message.
"""

import re
from typing import Any

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    ContextMenuContext,
    Embed,
    Modal,
    ModalContext,
    OptionType,
    ParagraphText,
    Permissions,
    SlashContext,
    message_context_menu,
    modal_callback,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.suggestions import Suggestion
from src.core import clock
from src.core.logging import log_failure
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.embeds import Colors
from src.discord_ext.menus import menu_help
from src.discord_ext.messages import (
    edit_message_if_changed,
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
    STATUS_COLORS,
    STATUS_LABELS,
    SUGGESTIONS,
    VOTE_PREFIX,
    get_guild_settings,
    logger,
)


def _build_embed(
    suggestion: Suggestion,
    *,
    anonymous: bool,
    bot_name: str = "",
) -> Embed:
    up, down = suggestion.tally()
    author_value = "Anonyme" if anonymous else f"<@{suggestion.author_id}>"

    embed = Embed(
        title=f"Suggestion #{suggestion.id}",
        description=suggestion.text,
        color=STATUS_COLORS.get(suggestion.status, Colors.UTIL),
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
    if bot_name:
        embed.set_footer(text=bot_name)
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


MAX_SUGGESTION_LENGTH = 1500
SUGGEST_MENU = "💡 En faire une suggestion"
SUGGEST_FORM = "suggest_from"  # suggest_from:{channel_id}:{message_id}
_SUGGEST_FORM_RE = re.compile(rf"^{SUGGEST_FORM}:(\d+):(\d+)$")


def suggest_form(channel_id: Any, message_id: Any, text: str) -> Modal:
    return Modal(
        ParagraphText(
            label="Ta suggestion",
            custom_id="text",
            placeholder="Reformule l'idée comme tu veux la proposer",
            value=text[:MAX_SUGGESTION_LENGTH] or None,
            required=True,
            max_length=MAX_SUGGESTION_LENGTH,
        ),
        title="Proposer en suggestion",
        custom_id=f"{SUGGEST_FORM}:{channel_id}:{message_id}",
    )


def _vote_components(sugg_id: int, *, disabled: bool = False) -> list[ActionRow]:
    return [
        ActionRow(
            Button(
                label="Pour",
                style=ButtonStyle.SUCCESS,
                emoji="👍",
                custom_id=f"{VOTE_PREFIX}:{sugg_id}:up",
                disabled=disabled,
            ),
            Button(
                label="Contre",
                style=ButtonStyle.DANGER,
                emoji="👎",
                custom_id=f"{VOTE_PREFIX}:{sugg_id}:down",
                disabled=disabled,
            ),
        )
    ]


class CommandsMixin:
    """Public ``/suggest`` and staff-only ``/suggestion ...`` sub-commands."""

    @slash_command(
        name="suggest",
        description="Proposer une suggestion à la communauté",
        scopes=guild_scopes(SUGGESTIONS.guild_ids()),
    )
    @slash_option(
        "texte",
        "Contenu de la suggestion",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="text",
    )
    async def suggest(self, ctx: SlashContext, text: str) -> None:
        if not await require_guild(ctx):
            return
        await self.publish_suggestion(ctx, text)

    @menu_help("Proposer ce message comme suggestion, à reformuler")
    @message_context_menu(name=SUGGEST_MENU, scopes=guild_scopes(SUGGESTIONS.guild_ids()))
    async def suggest_from_message(self, ctx: ContextMenuContext) -> None:
        message: Any = ctx.target
        await ctx.send_modal(suggest_form(ctx.channel_id, message.id, message.content or ""))

    @modal_callback(_SUGGEST_FORM_RE)
    async def on_suggest_form(self, ctx: ModalContext, text: str = "") -> None:
        match = _SUGGEST_FORM_RE.match(ctx.custom_id)
        if not match:
            return
        channel_id, message_id = match.groups()
        source = jump_url(guild_id_of(ctx), channel_id, message_id)
        await self.publish_suggestion(ctx, text.strip(), source_url=source)

    async def publish_suggestion(
        self, ctx: Any, text: str, *, source_url: str | None = None
    ) -> None:
        """Post *text* as a suggestion in the configured channel, for ``/suggest`` and the form."""
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
            await send_error(ctx, "Aucun salon de suggestions n'est configuré.")
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
            text=text,
            source_url=source_url,
            created_at=clock.now(),
        )
        await repo.add(suggestion)

        anonymous = bool(settings.get("anonymous", False))
        embed = _build_embed(suggestion, anonymous=anonymous)
        components = _vote_components(sugg_id)

        try:
            sent = await channel.send(embeds=[embed], components=components)  # type: ignore[union-attr]
        except Exception as e:
            log_failure(logger, "Failed to publish suggestion %s: %s", sugg_id, e)
            await send_error(ctx, "Impossible de publier la suggestion.")
            return

        await repo.set_message(sugg_id, str(sent.id))
        logger.info(
            "Suggestion #%s created by %s in guild %s", sugg_id, ctx.author.username, ctx.guild_id
        )
        await send_success(ctx, f"Suggestion **#{sugg_id}** publiée dans <#{channel.id}>.")

    @slash_command(
        name="suggestion",
        description="Gérer les suggestions (staff)",
        sub_cmd_name="approve",
        sub_cmd_description="Approuver une suggestion",
        scopes=guild_scopes(SUGGESTIONS.guild_ids()),
    )
    @slash_option(
        "id",
        "ID de la suggestion",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=1,
        argument_name="sugg_id",
    )
    @slash_option(
        "motif",
        "Motif optionnel",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="reason",
    )
    # Hidden from members (B9); the staff role is checked when it runs. A staff
    # role without "Gérer les messages" gets it back in the server's
    # Intégrations settings (the schema says so).
    @slash_default_member_permission(Permissions.MANAGE_MESSAGES)
    async def suggestion_approve(
        self, ctx: SlashContext, sugg_id: int, reason: str | None = None
    ) -> None:
        await self._handle_decision(ctx, sugg_id, "approved", reason)

    @suggestion_approve.subcommand(
        sub_cmd_name="deny",
        sub_cmd_description="Refuser une suggestion",
    )
    @slash_option(
        "id",
        "ID de la suggestion",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=1,
        argument_name="sugg_id",
    )
    @slash_option(
        "motif",
        "Motif optionnel",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="reason",
    )
    async def suggestion_deny(
        self, ctx: SlashContext, sugg_id: int, reason: str | None = None
    ) -> None:
        await self._handle_decision(ctx, sugg_id, "denied", reason)

    @suggestion_approve.subcommand(
        sub_cmd_name="implement",
        sub_cmd_description="Marquer une suggestion comme implémentée",
    )
    @slash_option(
        "id",
        "ID de la suggestion",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=1,
        argument_name="sugg_id",
    )
    @slash_option(
        "motif",
        "Note optionnelle",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="reason",
    )
    async def suggestion_implement(
        self, ctx: SlashContext, sugg_id: int, reason: str | None = None
    ) -> None:
        await self._handle_decision(ctx, sugg_id, "implemented", reason)

    async def _handle_decision(
        self,
        ctx: SlashContext,
        sugg_id: int,
        status: str,
        reason: str | None,
    ) -> None:
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

        await repo.update_status(sugg_id, status, reason, str(ctx.author.id))  # type: ignore[arg-type]
        suggestion.status = status  # type: ignore[assignment]
        suggestion.reason = reason
        suggestion.decided_by = str(ctx.author.id)

        anonymous = bool(settings.get("anonymous", False))
        embed = _build_embed(suggestion, anonymous=anonymous)

        if suggestion.message_id:
            try:
                channel = await fetch_guild_channel(
                    self.bot, guild_id_of(ctx), suggestion.channel_id, logger=logger
                )
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(int(suggestion.message_id))
                    if msg:
                        # Disable vote buttons once a decision has been made.
                        await edit_message_if_changed(
                            msg,
                            embeds=[embed],
                            components=_vote_components(sugg_id, disabled=True),
                            logger=logger,
                        )
                        if not anonymous:
                            await channel.send(  # type: ignore[union-attr]
                                f"<@{suggestion.author_id}> ta suggestion **#{sugg_id}** a "
                                f"été {STATUS_LABELS[status]}."
                            )
            except Exception as e:
                logger.warning("Could not update suggestion #%s message: %s", sugg_id, e)

        logger.info(
            "Suggestion #%s -> %s by %s (guild %s)",
            sugg_id,
            status,
            ctx.author.username,
            ctx.guild_id,
        )
        await send_success(ctx, f"Suggestion **#{sugg_id}** {STATUS_LABELS[status]}.")
