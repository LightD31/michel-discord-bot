"""Slash commands for member moderation + infraction history (CommandsMixin).

Member actions: ``/avertir``, ``/exclure``, ``/lever-exclusion``, ``/expulser``, ``/bannir``,
``/debannir``. History: ``/infraction liste|view|remove``. Every action records a
numbered :class:`Infraction` case, optionally DMs the target, and mirrors an
embed to the configured modlog channel.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

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
    ModerationRepository,
    clamp_timeout,
    humanize_duration,
    parse_duration,
)
from src.core import clock
from src.core.text import format_discord_timestamp
from src.discord_ext.errors import failure_reply
from src.discord_ext.look import card
from src.discord_ext.messages import (
    guild_id_of,
    guild_of,
    module_off,
    require_guild,
    send_error,
    send_success,
)
from src.discord_ext.scopes import guild_scopes

from ._common import (
    AUTOMOD_MODERATOR_ID,
    MODERATION,
    TYPE_LABELS,
    can_moderate,
    get_guild_settings,
    logger,
)
from .logging_ import build_case_embed

# Discord's cap on an audit-log reason: a longer one makes the action fail.
MAX_REASON_LENGTH = 512


class ActionRefused(Exception):
    """Discord refused a sanction (permissions, hierarchy, the member left): *cause* says why."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


class CommandsMixin:
    """Member-moderation commands and infraction-history sub-commands."""

    if TYPE_CHECKING:
        # __init__.py and logging_.py
        def repository(self, guild_id: str | int) -> ModerationRepository: ...

        async def log_case(
            self,
            settings: dict,
            infraction: Infraction,
            *,
            target_name: str | None = None,
            moderator_name: str | None = None,
        ) -> None: ...

        async def dm_target(self, settings: dict, target: Any, text: str) -> None: ...

    # --- shared helpers ----------------------------------------------------

    async def _guild_settings_or_error(self, ctx: SlashContext) -> dict | None:
        if not await require_guild(ctx):
            return None
        settings = get_guild_settings(guild_id_of(ctx))
        if not settings:
            await send_error(ctx, module_off("modération"), icon="🛡️")
            return None
        return settings

    async def _record(
        self,
        ctx: Any,
        *,
        mtype: InfractionType,
        target_id: str | int,
        reason: str | None,
        duration_seconds: int | None = None,
        expires_at: datetime | None = None,
    ) -> Infraction:
        repo = self.repository(guild_id_of(ctx))
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
            created_at=clock.now(),
        )
        await repo.add(infraction)
        return infraction

    @staticmethod
    def _reason_suffix(reason: str | None) -> str:
        return f"\nRaison : {reason}" if reason else ""

    # --- /avertir -------------------------------------------------------------

    @slash_command(
        name="avertir",
        description="Avertir un membre",
        scopes=guild_scopes(MODERATION.guild_ids()),
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
        max_length=MAX_REASON_LENGTH,
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
        infraction, count = await self.warn_member(ctx, settings, member, reason)
        await send_success(
            ctx,
            f"{member.mention} averti (cas **#{infraction.id}**). "
            f"Avertissements actifs : **{count}**.",
        )

    async def warn_member(
        self, ctx: Any, settings: dict, member: Any, reason: str | None
    ) -> tuple[Infraction, int]:
        """Record a warning, DM the member and log the case: ``(case, active warnings)``.

        Shared by ``/avertir``, the "⚠️ Avertir…" right-click and a report's card;
        the caller has checked :func:`can_moderate`.
        """
        infraction = await self._record(ctx, mtype="warn", target_id=member.id, reason=reason)
        count = await self.repository(guild_id_of(ctx)).count_active_warnings(str(member.id))
        await self.dm_target(
            settings,
            member,
            f"⚠️ Tu as reçu un avertissement sur **{guild_of(ctx).name}**."
            + self._reason_suffix(reason),
        )
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        return infraction, count

    # --- /exclure ----------------------------------------------------------

    @slash_command(
        name="exclure",
        description="Exclure temporairement un membre (timeout)",
        scopes=guild_scopes(MODERATION.guild_ids()),
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
        "raison",
        "Raison",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_REASON_LENGTH,
        argument_name="reason",
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
            await send_error(
                ctx,
                f"Je n'ai pas compris la durée « {duration} ». Exemples : `10m`, `1h30m`, `2d`.",
                icon="⏱️",
            )
            return
        secs = clamp_timeout(secs)
        await ctx.defer(ephemeral=True)
        try:
            infraction = await self.timeout_member(ctx, settings, member, secs, reason)
        except ActionRefused as e:
            await send_error(ctx, failure_reply("Impossible d'exclure ce membre", e.cause, logger))
            return
        await send_success(
            ctx,
            f"{member.mention} exclu pour **{humanize_duration(secs)}** (cas **#{infraction.id}**).",
        )

    async def timeout_member(
        self, ctx: Any, settings: dict, member: Any, secs: int, reason: str | None
    ) -> Infraction:
        """Time the member out for *secs*, record the case, DM them and log it.

        Raises :class:`ActionRefused` when Discord refused the timeout itself
        (nothing is recorded then). Shared by ``/exclure`` and a report's card.
        """
        until = datetime.now(UTC) + timedelta(seconds=secs)
        try:
            await member.timeout(
                communication_disabled_until=until,
                reason=reason or f"Timeout par {ctx.author.username}",
            )
        except Exception as e:
            raise ActionRefused(e) from e
        infraction = await self._record(
            ctx,
            mtype="timeout",
            target_id=member.id,
            reason=reason,
            duration_seconds=secs,
            expires_at=clock.now() + timedelta(seconds=secs),
        )
        await self.dm_target(
            settings,
            member,
            f"🔇 Tu as été exclu temporairement sur **{guild_of(ctx).name}** pour "
            f"**{humanize_duration(secs)}**." + self._reason_suffix(reason),
        )
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        return infraction

    # --- /lever-exclusion --------------------------------------------------------

    @slash_command(
        name="lever-exclusion",
        description="Lever l'exclusion temporaire d'un membre",
        scopes=guild_scopes(MODERATION.guild_ids()),
    )
    @slash_option(
        "membre", "Membre concerné", opt_type=OptionType.USER, required=True, argument_name="member"
    )
    @slash_option(
        "raison",
        "Raison",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_REASON_LENGTH,
        argument_name="reason",
    )
    @slash_default_member_permission(Permissions.MODERATE_MEMBERS)
    async def untimeout(self, ctx: SlashContext, member: Member, reason: str | None = None) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        # Lifting a timeout follows the same hierarchy as setting one: a
        # moderator can't undo a sanction on a member who outranks them.
        guard = can_moderate(ctx.author, member, getattr(ctx.guild, "me", None))
        if guard:
            await send_error(ctx, guard)
            return
        await ctx.defer(ephemeral=True)
        try:
            await member.timeout(
                communication_disabled_until=None,
                reason=reason or f"Fin d'exclusion par {ctx.author.username}",
            )
        except Exception as e:
            await send_error(ctx, failure_reply("Impossible de lever l'exclusion", e, logger))
            return
        infraction = await self._record(ctx, mtype="untimeout", target_id=member.id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"Exclusion de {member.mention} levée (cas **#{infraction.id}**).")

    # --- /expulser -------------------------------------------------------------

    @slash_command(
        name="expulser",
        description="Expulser un membre",
        scopes=guild_scopes(MODERATION.guild_ids()),
    )
    @slash_option(
        "membre",
        "Membre à expulser",
        opt_type=OptionType.USER,
        required=True,
        argument_name="member",
    )
    @slash_option(
        "raison",
        "Raison",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_REASON_LENGTH,
        argument_name="reason",
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
            f"👢 Tu as été expulsé de **{guild_of(ctx).name}**." + self._reason_suffix(reason),
        )
        try:
            await guild_of(ctx).kick(member, reason=reason or f"Kick par {ctx.author.username}")
        except Exception as e:
            # The DM went first (afterwards the bot may share no server with
            # them): correct it.
            await self.dm_target(
                settings,
                member,
                f"Finalement, tu n'as pas été expulsé de **{guild_of(ctx).name}**.",
            )
            await send_error(ctx, failure_reply("Impossible d'expulser ce membre", e, logger))
            return
        infraction = await self._record(ctx, mtype="kick", target_id=member.id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"{member.mention} expulsé (cas **#{infraction.id}**).")

    # --- /bannir --------------------------------------------------------------

    @slash_command(
        name="bannir",
        description="Bannir un membre",
        scopes=guild_scopes(MODERATION.guild_ids()),
    )
    @slash_option(
        "membre", "Membre à bannir", opt_type=OptionType.USER, required=True, argument_name="member"
    )
    @slash_option(
        "raison",
        "Raison",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_REASON_LENGTH,
        argument_name="reason",
    )
    @slash_option(
        "jours-messages",
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
            f"🔨 Tu as été banni de **{guild_of(ctx).name}**." + self._reason_suffix(reason),
        )
        try:
            await guild_of(ctx).ban(
                member,
                # Not the deprecated delete_message_days: the library turns it
                # into days * 3600 seconds, so "7 days" deleted 7 hours.
                delete_message_seconds=delete_days * 86400,
                reason=reason or f"Ban par {ctx.author.username}",
            )
        except Exception as e:
            # The DM went first (afterwards the bot may share no server with
            # them): correct it.
            await self.dm_target(
                settings, member, f"Finalement, tu n'as pas été banni de **{guild_of(ctx).name}**."
            )
            await send_error(ctx, failure_reply("Impossible de bannir ce membre", e, logger))
            return
        infraction = await self._record(ctx, mtype="ban", target_id=member.id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=member.mention, moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"{member.mention} banni (cas **#{infraction.id}**).")

    # --- /debannir ------------------------------------------------------------

    @slash_command(
        name="debannir",
        description="Révoquer le bannissement d'un utilisateur",
        scopes=guild_scopes(MODERATION.guild_ids()),
    )
    @slash_option(
        "utilisateur-id",
        "ID Discord de l'utilisateur à débannir",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="user_id",
    )
    @slash_option(
        "raison",
        "Raison",
        opt_type=OptionType.STRING,
        required=False,
        max_length=MAX_REASON_LENGTH,
        argument_name="reason",
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
            await guild_of(ctx).unban(
                int(user_id), reason=reason or f"Unban par {ctx.author.username}"
            )
        except Exception as e:
            await send_error(
                ctx, failure_reply("Impossible de débannir cet utilisateur", e, logger)
            )
            return
        infraction = await self._record(ctx, mtype="unban", target_id=user_id, reason=reason)
        await self.log_case(
            settings, infraction, target_name=f"<@{user_id}>", moderator_name=ctx.author.mention
        )
        await send_success(ctx, f"<@{user_id}> débanni (cas **#{infraction.id}**).")

    # --- /infraction liste|view|remove -------------------------------------

    def _build_history_embed(self, member: Member, items: list[Infraction]) -> Embed:
        active = sum(1 for i in items if i.active)
        older = len(items) - 15
        embed = card(
            "moduleModeration",
            title=f"Historique de {member.display_name}",
            description=f"{len(items)} sanction(s) · {active} active(s)",
            footer=f"+{older} sanction(s) plus ancienne(s)" if older > 0 else None,
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
        return embed

    @slash_command(
        name="infraction",
        description="Consulter et gérer l'historique de modération",
        sub_cmd_name="liste",
        sub_cmd_description="Lister les sanctions d'un membre",
        scopes=guild_scopes(MODERATION.guild_ids()),
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
        items = await self.repository(guild_id_of(ctx)).list_for_user(str(member.id))
        if not items:
            await send_success(ctx, f"Aucune sanction enregistrée pour {member.mention}.")
            return
        await ctx.send(embeds=[self._build_history_embed(member, items)], ephemeral=True)

    @infraction_list.subcommand(
        sub_cmd_name="voir", sub_cmd_description="Afficher une sanction précise"
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
        infraction = await self.repository(guild_id_of(ctx)).get(case_id)
        if not infraction:
            await send_error(ctx, f"Cas #{case_id} introuvable.")
            return
        await ctx.send(embeds=[build_case_embed(infraction)], ephemeral=True)

    @infraction_list.subcommand(
        sub_cmd_name="retirer", sub_cmd_description="Révoquer une sanction de l'historique"
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
        max_length=MAX_REASON_LENGTH,
        argument_name="reason",
    )
    async def infraction_remove(
        self, ctx: SlashContext, case_id: int, reason: str | None = None
    ) -> None:
        settings = await self._guild_settings_or_error(ctx)
        if settings is None:
            return
        repo = self.repository(guild_id_of(ctx))
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
