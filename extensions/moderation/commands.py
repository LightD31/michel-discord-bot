"""Slash commands for member moderation + infraction history (CommandsMixin).

Member actions: ``/warn``, ``/timeout``, ``/untimeout``, ``/kick``, ``/ban``,
``/unban``. History: ``/infraction list|view|remove``. Every action records a
numbered :class:`Infraction` case, optionally DMs the target, and mirrors an
embed to the configured modlog channel.
"""

from datetime import UTC, datetime, timedelta

from interactions import (
    Embed,
    Member,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.moderation import (
    Infraction,
    InfractionType,
    clamp_timeout,
    humanize_duration,
    parse_duration,
)
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.messages import require_guild, send_error, send_success

from ._common import (
    AUTOMOD_MODERATOR_ID,
    TYPE_LABELS,
    can_moderate,
    enabled_servers_int,
    get_guild_settings,
    logger,
)
from .logging_ import build_case_embed


class CommandsMixin:
    """Member-moderation commands and infraction-history sub-commands."""

    # --- shared helpers ----------------------------------------------------

    async def _guild_settings_or_error(self, ctx: SlashContext) -> dict | None:
        if not await require_guild(ctx):
            return None
        settings = get_guild_settings(ctx.guild_id)
        if not settings:
            await send_error(ctx, "Le module de modération n'est pas activé sur ce serveur.")
            return None
        return settings

    async def _record(
        self,
        ctx: SlashContext,
        *,
        mtype: InfractionType,
        target_id: str | int,
        reason: str | None,
        duration_seconds: int | None = None,
        expires_at: datetime | None = None,
    ) -> Infraction:
        repo = self.repository(ctx.guild_id)
        case_id = await repo.next_case_id()
        infraction = Infraction(
            id=case_id,
            guild_id=str(ctx.guild_id),
            user_id=str(target_id),
            moderator_id=str(ctx.author.id),
            type=mtype,
            reason=reason,
            duration_seconds=duration_seconds,
            expires_at=expires_at,
            created_at=datetime.now(),
        )
        await repo.add(infraction)
        return infraction

    @staticmethod
    def _reason_suffix(reason: str | None) -> str:
        return f"\nRaison : {reason}" if reason else ""

    # --- /warn -------------------------------------------------------------

    @slash_command(
        name="warn",
        description="Avertir un membre",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "membre",
        "Membre à avertir",
        opt_type=OptionType.USER,
        required=True,
        argument_name="member",
    )
    @slash_option(
        "raison",
        "Raison de l'avertissement",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="reason",
    )
    @slash_default_member_permission(Permissions.MODERATE_MEMBERS)
    async def warn(self, ctx: SlashContext, member: Member, reason: str | None = None) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        guard = can_moderate(ctx.author, member, getattr(ctx.guild, "me", None))
        if guard:
            await send_error(ctx, guard)
            return
        await ctx.defer(ephemeral=True)
        infraction = await self._record(ctx, mtype="warn", target_id=member.id, reason=reason)
        count = await self.repository(ctx.guild_id).count_active_warnings(str(member.id))
        await self.dm_target(
            settings,
            member,
            f"⚠️ Tu as reçu un avertissement sur **{ctx.guild.name}**."
            + self._reason_suffix(reason),
        )
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(
            ctx,
            f"{member.mention} averti (cas **#{infraction.id}**). "
            f"Avertissements actifs : **{count}**.",
        )

    # --- /timeout ----------------------------------------------------------

    @slash_command(
        name="timeout",
        description="Exclure temporairement un membre (timeout)",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "membre",
        "Membre à exclure",
        opt_type=OptionType.USER,
        required=True,
        argument_name="member",
    )
    @slash_option(
        "duree",
        "Durée (ex: 10m, 1h30m, 2d — max 28 j)",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="duration",
    )
    @slash_option(
        "raison", "Raison", opt_type=OptionType.STRING, required=False, argument_name="reason"
    )
    @slash_default_member_permission(Permissions.MODERATE_MEMBERS)
    async def timeout(
        self, ctx: SlashContext, member: Member, duration: str, reason: str | None = None
    ) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        guard = can_moderate(ctx.author, member, getattr(ctx.guild, "me", None))
        if guard:
            await send_error(ctx, guard)
            return
        secs = parse_duration(duration)
        if not secs:
            await send_error(ctx, "Durée invalide. Exemples : `10m`, `1h30m`, `2d`.")
            return
        secs = clamp_timeout(secs)
        await ctx.defer(ephemeral=True)
        until = datetime.now(UTC) + timedelta(seconds=secs)
        try:
            await member.timeout(
                communication_disabled_until=until,
                reason=reason or f"Timeout par {ctx.author.username}",
            )
        except Exception as e:
            logger.warning("timeout failed for %s: %s", member.id, e)
            await send_error(ctx, f"Impossible d'exclure ce membre : {e}")
            return
        infraction = await self._record(
            ctx,
            mtype="timeout",
            target_id=member.id,
            reason=reason,
            duration_seconds=secs,
            expires_at=datetime.now() + timedelta(seconds=secs),
        )
        await self.dm_target(
            settings,
            member,
            f"🔇 Tu as été exclu temporairement sur **{ctx.guild.name}** pour "
            f"**{humanize_duration(secs)}**." + self._reason_suffix(reason),
        )
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(
            ctx,
            f"{member.mention} exclu pour **{humanize_duration(secs)}** (cas **#{infraction.id}**).",
        )

    # --- /untimeout --------------------------------------------------------

    @slash_command(
        name="untimeout",
        description="Lever l'exclusion temporaire d'un membre",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "membre", "Membre concerné", opt_type=OptionType.USER, required=True, argument_name="member"
    )
    @slash_option(
        "raison", "Raison", opt_type=OptionType.STRING, required=False, argument_name="reason"
    )
    @slash_default_member_permission(Permissions.MODERATE_MEMBERS)
    async def untimeout(self, ctx: SlashContext, member: Member, reason: str | None = None) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        await ctx.defer(ephemeral=True)
        try:
            await member.timeout(
                communication_disabled_until=None,
                reason=reason or f"Fin d'exclusion par {ctx.author.username}",
            )
        except Exception as e:
            await send_error(ctx, f"Impossible de lever l'exclusion : {e}")
            return
        infraction = await self._record(ctx, mtype="untimeout", target_id=member.id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"Exclusion de {member.mention} levée (cas **#{infraction.id}**).")

    # --- /kick -------------------------------------------------------------

    @slash_command(
        name="kick",
        description="Expulser un membre",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "membre",
        "Membre à expulser",
        opt_type=OptionType.USER,
        required=True,
        argument_name="member",
    )
    @slash_option(
        "raison", "Raison", opt_type=OptionType.STRING, required=False, argument_name="reason"
    )
    @slash_default_member_permission(Permissions.KICK_MEMBERS)
    async def kick(self, ctx: SlashContext, member: Member, reason: str | None = None) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        guard = can_moderate(ctx.author, member, getattr(ctx.guild, "me", None))
        if guard:
            await send_error(ctx, guard)
            return
        await ctx.defer(ephemeral=True)
        await self.dm_target(
            settings,
            member,
            f"👢 Tu as été expulsé de **{ctx.guild.name}**." + self._reason_suffix(reason),
        )
        try:
            await ctx.guild.kick(member, reason=reason or f"Kick par {ctx.author.username}")
        except Exception as e:
            await send_error(ctx, f"Impossible d'expulser ce membre : {e}")
            return
        infraction = await self._record(ctx, mtype="kick", target_id=member.id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"{member.mention} expulsé (cas **#{infraction.id}**).")

    # --- /ban --------------------------------------------------------------

    @slash_command(
        name="ban",
        description="Bannir un membre",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "membre", "Membre à bannir", opt_type=OptionType.USER, required=True, argument_name="member"
    )
    @slash_option(
        "raison", "Raison", opt_type=OptionType.STRING, required=False, argument_name="reason"
    )
    @slash_option(
        "jours_messages",
        "Jours de messages à supprimer (0-7)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=0,
        max_value=7,
        argument_name="delete_days",
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def ban(
        self,
        ctx: SlashContext,
        member: Member,
        reason: str | None = None,
        delete_days: int = 0,
    ) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        guard = can_moderate(ctx.author, member, getattr(ctx.guild, "me", None))
        if guard:
            await send_error(ctx, guard)
            return
        await ctx.defer(ephemeral=True)
        await self.dm_target(
            settings,
            member,
            f"🔨 Tu as été banni de **{ctx.guild.name}**." + self._reason_suffix(reason),
        )
        try:
            await ctx.guild.ban(
                member,
                delete_message_days=delete_days,
                reason=reason or f"Ban par {ctx.author.username}",
            )
        except Exception as e:
            await send_error(ctx, f"Impossible de bannir ce membre : {e}")
            return
        infraction = await self._record(ctx, mtype="ban", target_id=member.id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"{member.mention} banni (cas **#{infraction.id}**).")

    # --- /unban ------------------------------------------------------------

    @slash_command(
        name="unban",
        description="Révoquer le bannissement d'un utilisateur",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "utilisateur_id",
        "ID Discord de l'utilisateur à débannir",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="user_id",
    )
    @slash_option(
        "raison", "Raison", opt_type=OptionType.STRING, required=False, argument_name="reason"
    )
    @slash_default_member_permission(Permissions.BAN_MEMBERS)
    async def unban(self, ctx: SlashContext, user_id: str, reason: str | None = None) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        if not user_id.isdigit():
            await send_error(ctx, "L'ID utilisateur doit être numérique.")
            return
        await ctx.defer(ephemeral=True)
        try:
            await ctx.guild.unban(int(user_id), reason=reason or f"Unban par {ctx.author.username}")
        except Exception as e:
            await send_error(ctx, f"Impossible de débannir cet utilisateur : {e}")
            return
        infraction = await self._record(ctx, mtype="unban", target_id=user_id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=f"<@{user_id}>", moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"<@{user_id}> débanni (cas **#{infraction.id}**).")

    # --- /infraction list|view|remove -------------------------------------

    def _build_history_embed(self, member: Member, items: list[Infraction]) -> Embed:
        active = sum(1 for i in items if i.active)
        embed = Embed(
            title=f"Historique de {member.display_name}",
            description=f"{len(items)} sanction(s) · {active} active(s)",
            color=Colors.UTIL,
        )
        for inf in items[:15]:
            ts = format_discord_timestamp(inf.created_at, "d")
            dur = f" · {humanize_duration(inf.duration_seconds)}" if inf.duration_seconds else ""
            status = "" if inf.active else " · ❌ révoqué"
            by = (
                "🛡️ Automod"
                if inf.moderator_id == AUTOMOD_MODERATOR_ID
                else f"<@{inf.moderator_id}>"
            )
            embed.add_field(
                name=f"#{inf.id} · {TYPE_LABELS.get(inf.type, inf.type)}{dur}{status}",
                value=f"{(inf.reason or '*Aucune raison*')[:300]}\nPar {by} · {ts}",
                inline=False,
            )
        if len(items) > 15:
            embed.set_footer(text=f"+{len(items) - 15} sanction(s) plus ancienne(s)")
        return embed

    @slash_command(
        name="infraction",
        description="Consulter et gérer l'historique de modération",
        sub_cmd_name="list",
        sub_cmd_description="Lister les sanctions d'un membre",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "membre", "Membre concerné", opt_type=OptionType.USER, required=True, argument_name="member"
    )
    @slash_default_member_permission(Permissions.MODERATE_MEMBERS)
    async def infraction_list(self, ctx: SlashContext, member: Member) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        await ctx.defer(ephemeral=True)
        items = await self.repository(ctx.guild_id).list_for_user(str(member.id))
        if not items:
            await send_success(ctx, f"Aucune sanction enregistrée pour {member.mention}.")
            return
        await ctx.send(embeds=[self._build_history_embed(member, items)], ephemeral=True)

    @infraction_list.subcommand(
        sub_cmd_name="view", sub_cmd_description="Afficher une sanction précise"
    )
    @slash_option(
        "id",
        "Numéro de cas",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=1,
        argument_name="case_id",
    )
    async def infraction_view(self, ctx: SlashContext, case_id: int) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        infraction = await self.repository(ctx.guild_id).get(case_id)
        if not infraction:
            await send_error(ctx, f"Cas #{case_id} introuvable.")
            return
        await ctx.send(embeds=[build_case_embed(infraction)], ephemeral=True)

    @infraction_list.subcommand(
        sub_cmd_name="remove", sub_cmd_description="Révoquer une sanction de l'historique"
    )
    @slash_option(
        "id",
        "Numéro de cas",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=1,
        argument_name="case_id",
    )
    @slash_option(
        "raison",
        "Raison de la révocation",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="reason",
    )
    async def infraction_remove(
        self, ctx: SlashContext, case_id: int, reason: str | None = None
    ) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        repo = self.repository(ctx.guild_id)
        infraction = await repo.get(case_id)
        if not infraction:
            await send_error(ctx, f"Cas #{case_id} introuvable.")
            return
        if not infraction.active:
            await send_error(ctx, f"Le cas #{case_id} est déjà révoqué.")
            return
        await repo.set_active(case_id, False)
        infraction.active = False
        infraction.reason = reason or infraction.reason
        await self.log_case(settings, infraction, moderator_name=ctx.author.mention)
        await send_success(ctx, f"Cas **#{case_id}** révoqué.")
