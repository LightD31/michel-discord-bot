"""`/uptime setup|remove|list` admin commands for managing monitor alerts."""

from interactions import (
    AutocompleteContext,
    BaseChannel,
    ChannelType,
    Embed,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from features.uptime import AlertMode, MonitorAlert, UptimeRepository
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.discord_ext.autocomplete import is_guild_enabled
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild, send_error, send_success
from src.discord_ext.scopes import guild_scopes
from src.integrations.uptime_kuma import MonitorStatus

from ._common import (
    KUMA_UNREACHABLE,
    MAX_CHOICE_NAME,
    MAX_CHOICES,
    MAX_EMBED_FIELDS,
    enabled_servers,
)

logger = logutil.init_logger(__name__)


def _choice_name(name: str) -> str:
    return name if len(name) <= MAX_CHOICE_NAME else name[: MAX_CHOICE_NAME - 1] + "…"


class CommandsMixin:
    """Admin `/uptime` command group + monitor autocompletes."""

    async def _check_guild(self, ctx: SlashContext) -> str | None:
        """The guild id, or ``None`` after telling the user why the command can't run."""
        if not await require_guild(ctx):
            return None
        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, "Le module Uptime n'est pas activé sur ce serveur.")
            return None
        return str(ctx.guild.id)

    @staticmethod
    def _parse_monitor_id(value: str) -> int | None:
        value = value.strip()
        return int(value) if value.isdigit() else None

    @slash_command(
        name="uptime",
        description="Les commandes de surveillance Uptime Kuma",
        scopes=guild_scopes(enabled_servers),
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR)
    async def uptime_command(self, ctx: SlashContext) -> None:
        pass

    @uptime_command.subcommand(
        sub_cmd_name="setup",
        sub_cmd_description="Configure les alertes de maintenance pour un capteur spécifique",
    )
    @slash_option(
        name="sensor",
        description="Nom du capteur à surveiller",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="channel",
        description="Canal où envoyer les notifications",
        opt_type=OptionType.CHANNEL,
        required=True,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
    )
    @slash_option(
        name="mode",
        description="Mode d'affichage des notifications",
        opt_type=OptionType.STRING,
        required=False,
        choices=[
            {"name": "Simple (titre et statut seulement)", "value": AlertMode.SIMPLE.value},
            {"name": "Détaillé (avec toutes les informations)", "value": AlertMode.DETAILED.value},
        ],
    )
    async def setup_maintenance_alert(
        self,
        ctx: SlashContext,
        sensor: str,
        channel: BaseChannel,
        mode: str = AlertMode.DETAILED.value,
    ):
        guild_id = await self._check_guild(ctx)
        if guild_id is None:
            return
        if not self.kuma_ok:
            await send_error(ctx, KUMA_UNREACHABLE)
            return
        monitor_id = self._parse_monitor_id(sensor)
        monitor = self.monitors.get(monitor_id) if monitor_id is not None else None
        if monitor is None:
            await send_error(ctx, "Capteur introuvable. Choisissez-le dans la liste proposée.")
            return

        alert = MonitorAlert(
            monitor_id=monitor.id,
            channel_id=int(channel.id),
            mode=AlertMode.parse(mode),
            # The status Kuma reports right now is the baseline, so the next
            # change posts instead of silently setting it. PENDING isn't settled.
            last_status=None if monitor.status is MonitorStatus.PENDING else monitor.status,
        )
        try:
            await UptimeRepository(guild_id).save(alert)
        except DatabaseError as e:
            logger.error("Guild %s: could not save uptime alert %s: %s", guild_id, monitor.id, e)
            await send_error(ctx, "Impossible d'enregistrer l'alerte, réessayez plus tard.")
            return
        async with self._state_lock:
            self.alerts.setdefault(guild_id, {})[monitor.id] = alert

        logger.info(
            "Guild %s: %s set an uptime alert for monitor %s in channel %s (%s)",
            guild_id,
            ctx.author.id,
            monitor.id,
            channel.id,
            alert.mode.value,
        )
        await send_success(
            ctx,
            f"Les notifications du capteur **{monitor.name}** seront envoyées dans "
            f"{channel.mention} (mode : {alert.mode.label.lower()}).",
        )

    @setup_maintenance_alert.autocomplete("sensor")
    async def setup_sensor_autocomplete(self, ctx: AutocompleteContext):
        """Every Kuma monitor whose name (with its group) matches the input."""
        query = (ctx.input_text or "").lower()
        monitors = sorted(self.monitors.values(), key=lambda m: m.name.lower())
        choices = [
            {"name": _choice_name(m.name), "value": str(m.id)}
            for m in monitors
            if query in m.name.lower()
        ]
        await ctx.send(choices=choices[:MAX_CHOICES])

    @uptime_command.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Supprime les alertes de maintenance pour un capteur",
    )
    @slash_option(
        name="sensor",
        description="Nom du capteur à ne plus surveiller",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def remove_maintenance_alert(self, ctx: SlashContext, sensor: str):
        guild_id = await self._check_guild(ctx)
        if guild_id is None:
            return
        monitor_id = self._parse_monitor_id(sensor)
        if monitor_id is None or monitor_id not in self.alerts.get(guild_id, {}):
            await send_error(ctx, "Aucune alerte configurée pour ce capteur.")
            return

        try:
            await UptimeRepository(guild_id).remove(monitor_id)
        except DatabaseError as e:
            logger.error("Guild %s: could not remove uptime alert %s: %s", guild_id, monitor_id, e)
            await send_error(ctx, "Impossible de supprimer l'alerte, réessayez plus tard.")
            return
        async with self._state_lock:
            self.alerts.get(guild_id, {}).pop(monitor_id, None)

        logger.info(
            "Guild %s: %s removed the uptime alert for monitor %s",
            guild_id,
            ctx.author.id,
            monitor_id,
        )
        await send_success(
            ctx, f"Alerte supprimée pour le capteur **{self.monitor_name(monitor_id)}**."
        )

    @remove_maintenance_alert.autocomplete("sensor")
    async def remove_sensor_autocomplete(self, ctx: AutocompleteContext):
        """This guild's alerts, including monitors since deleted from Kuma."""
        if not ctx.guild:
            await ctx.send(choices=[])
            return
        query = (ctx.input_text or "").lower()
        names = {
            monitor_id: self.monitor_name(monitor_id)
            for monitor_id in self.alerts.get(str(ctx.guild.id), {})
        }
        choices = [
            {"name": _choice_name(name), "value": str(monitor_id)}
            for monitor_id, name in sorted(names.items(), key=lambda item: item[1].lower())
            if query in name.lower()
        ]
        await ctx.send(choices=choices[:MAX_CHOICES])

    @uptime_command.subcommand(
        sub_cmd_name="list",
        sub_cmd_description="Liste toutes les alertes de maintenance configurées",
    )
    async def list_maintenance_alerts(self, ctx: SlashContext):
        guild_id = await self._check_guild(ctx)
        if guild_id is None:
            return
        alerts = sorted(
            self.alerts.get(guild_id, {}).values(),
            key=lambda a: self.monitor_name(a.monitor_id).lower(),
        )
        if not alerts:
            await send_error(ctx, "Aucune alerte de maintenance configurée sur ce serveur.")
            return

        embed = Embed(title="📋 Alertes de maintenance configurées", color=Colors.INFO)
        for alert in alerts[:MAX_EMBED_FIELDS]:
            # Not ``or``: DOWN is 0.
            status = self.current_status(alert.monitor_id)
            if status is None:
                status = alert.last_status
            embed.add_field(
                name=f"Capteur : {self.monitor_name(alert.monitor_id)}",
                value=(
                    f"Canal : <#{alert.channel_id}>\n"
                    f"Mode : {alert.mode.label}\n"
                    f"État : {status.label if status is not None else 'inconnu'}"
                ),
                inline=False,
            )
        if len(alerts) > MAX_EMBED_FIELDS:
            embed.set_footer(f"{len(alerts) - MAX_EMBED_FIELDS} alerte(s) non affichée(s)")
        if not self.kuma_ok:
            embed.description = "⚠️ Uptime Kuma est injoignable : les états peuvent être anciens."
        await ctx.send(embed=embed, ephemeral=True)
