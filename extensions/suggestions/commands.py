"""Slash commands: ``/suggest`` (anyone) and ``/suggestion approve|deny|implement`` (staff)."""

from datetime import datetime

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Embed,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_option,
)

from features.suggestions import Suggestion
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild, send_error, send_success

from ._common import (
    STATUS_COLORS,
    STATUS_LABELS,
    VOTE_PREFIX,
    enabled_servers_int,
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


def _is_staff(ctx: SlashContext, settings: dict) -> bool:
    """True if the caller has the configured staff role, or administrator perms."""
    member = ctx.author
    try:
        if member.has_permission(Permissions.ADMINISTRATOR):
            return True
    except Exception:
        pass
    staff_role_id = settings.get("staffRoleId")
    if not staff_role_id:
        return False
    try:
        return any(int(getattr(r, "id", r)) == int(staff_role_id) for r in member.roles)
    except Exception:
        return False


class CommandsMixin:
    """Public ``/suggest`` and staff-only ``/suggestion ...`` sub-commands."""

    @slash_command(
        name="suggest",
        description="Proposer une suggestion à la communauté",
        scopes=enabled_servers_int,  # type: ignore
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
        settings = get_guild_settings(ctx.guild_id)
        if not settings:
            await send_error(ctx, "Le module suggestions n'est pas activé sur ce serveur.")
            return

        if len(text) > 1500:
            await send_error(ctx, "La suggestion est trop longue (max 1500 caractères).")
            return

        channel_id = settings.get("suggestChannelId")
        if not channel_id:
            await send_error(ctx, "Aucun salon de suggestions n'est configuré.")
            return

        try:
            channel = await self.bot.fetch_channel(int(channel_id))
        except Exception:
            channel = None
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
            created_at=datetime.now(),
        )
        await repo.add(suggestion)

        anonymous = bool(settings.get("anonymous", False))
        embed = _build_embed(suggestion, anonymous=anonymous)
        components = _vote_components(sugg_id)

        try:
            sent = await channel.send(embeds=[embed], components=components)  # type: ignore[union-attr]
        except Exception as e:
            logger.error("Failed to publish suggestion %s: %s", sugg_id, e)
            await send_error(ctx, "Impossible de publier la suggestion.")
            return

        await repo.set_message(sugg_id, str(sent.id))
        logger.info(
            "Suggestion #%s created by %s in guild %s", sugg_id, ctx.author.username, ctx.guild_id
        )
        await send_success(ctx, f"Suggestion **#{sugg_id}** publiée.")

    @slash_command(
        name="suggestion",
        description="Gérer les suggestions (staff)",
        sub_cmd_name="approve",
        sub_cmd_description="Approuver une suggestion",
        scopes=enabled_servers_int,  # type: ignore
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
            await send_error(ctx, "Le module suggestions n'est pas activé sur ce serveur.")
            return
        if not _is_staff(ctx, settings):
            await send_error(ctx, "Réservé au staff.")
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
                channel = await self.bot.fetch_channel(int(suggestion.channel_id))
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(int(suggestion.message_id))
                    if msg:
                        # Disable vote buttons once a decision has been made.
                        await msg.edit(
                            embeds=[embed],
                            components=_vote_components(sugg_id, disabled=True),
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
