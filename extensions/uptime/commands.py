"""`/uptime setup|remove|list` admin commands for managing maintenance alerts."""

import os

from interactions import (
    AutocompleteContext,
    BaseChannel,
    Embed,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_default_member_permission,
    slash_option,
)

from src.core import logging as logutil
from src.discord_ext.autocomplete import is_guild_enabled
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import send_error

from ._common import config, enabled_servers

logger = logutil.init_logger(os.path.basename(__file__))


class CommandsMixin:
    """Admin `/uptime` command group + per-sensor autocomplete."""

    @slash_command(name="uptime", description="Les commandes de surveillance Uptime Kuma")
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
    )
    @slash_option(
        name="mode",
        description="Mode d'affichage des notifications",
        opt_type=OptionType.STRING,
        required=False,
        choices=[
            {"name": "Simple (titre et statut seulement)", "value": "simple"},
            {"name": "Détaillé (avec toutes les informations)", "value": "detailed"},
        ],
    )
    async def setup_maintenance_alert(
        self, ctx: SlashContext, sensor: str, channel: BaseChannel, mode: str = "detailed"
    ):
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return

        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, "Le module Uptime n'est pas activé sur ce serveur.")
            return

        guild_id = str(ctx.guild.id)

        if (
            not config.get("uptimeKuma", {}).get("uptimeKumaUrl")
            or not config.get("uptimeKuma", {}).get("uptimeKumaUsername")
            or not config.get("uptimeKuma", {}).get("uptimeKumaPassword")
        ):
            await send_error(
                ctx,
                "Configuration Uptime Kuma manquante. Vérifiez l'URL, le nom d'utilisateur et le mot de passe.",
            )
            return

        try:
            sensor_id = int(sensor)
        except ValueError:
            await send_error(ctx, "ID de capteur invalide.")
            return

        sensor_info = await self._get_sensor_info(sensor_id)
        if not sensor_info:
            await send_error(ctx, f"Capteur avec l'ID {sensor_id} introuvable.")
            return

        if guild_id not in self.maintenance_monitors:
            self.maintenance_monitors[guild_id] = {}

        self.maintenance_monitors[guild_id][str(sensor_id)] = {
            "channel_id": channel.id,
            "last_status": None,
            "mode": mode,
        }

        await self.save_maintenance_monitors()

        embed = Embed(
            title="✅ Alerte de maintenance configurée",
            description=f"Les notifications de maintenance pour le capteur **{sensor_info.get('name', f'ID {sensor_id}')}** seront envoyées dans {channel.mention} (Mode: {mode})",
            color=Colors.SUCCESS,
        )
        await ctx.send(embed=embed)

    @setup_maintenance_alert.autocomplete("sensor")
    async def setup_sensor_autocomplete(self, ctx: AutocompleteContext):
        await self.sensor_autocomplete(ctx)

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
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return

        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, "Le module Uptime n'est pas activé sur ce serveur.")
            return

        guild_id = str(ctx.guild.id)

        try:
            sensor_id = int(sensor)
            sensor_id_str = str(sensor_id)
        except ValueError:
            await send_error(ctx, "ID de capteur invalide.")
            return

        if (
            guild_id not in self.maintenance_monitors
            or sensor_id_str not in self.maintenance_monitors[guild_id]
        ):
            await send_error(ctx, f"Aucune alerte configurée pour le capteur ID {sensor_id}.")
            return

        del self.maintenance_monitors[guild_id][sensor_id_str]

        if not self.maintenance_monitors[guild_id]:
            del self.maintenance_monitors[guild_id]

        await self.save_maintenance_monitors()

        await ctx.send(f"✅ Alerte de maintenance supprimée pour le capteur ID {sensor_id}.")

    @remove_maintenance_alert.autocomplete("sensor")
    async def remove_sensor_autocomplete(self, ctx: AutocompleteContext):
        """Only surfaces sensors currently tracked on this guild."""
        try:
            if not ctx.guild:
                await ctx.send(choices=[])
                return

            guild_id = str(ctx.guild.id)

            if guild_id not in self.maintenance_monitors:
                await ctx.send(choices=[])
                return

            monitored_sensors = []
            query = ctx.input_text.lower() if ctx.input_text else ""

            for sensor_id_str in self.maintenance_monitors[guild_id]:
                try:
                    sensor_id = int(sensor_id_str)
                    sensor_info = await self._get_sensor_info(sensor_id)

                    if sensor_info and "name" in sensor_info:
                        sensor_name = sensor_info["name"]
                        if query in sensor_name.lower():
                            display_name = (
                                sensor_name[:97] + "..." if len(sensor_name) > 100 else sensor_name
                            )
                            monitored_sensors.append({"name": display_name, "value": sensor_id_str})
                except (ValueError, TypeError):
                    continue

            await ctx.send(choices=monitored_sensors[:25])

        except Exception as error:
            logger.error(f"Erreur dans l'autocomplétion des capteurs surveillés: {error}")
            await ctx.send(choices=[])

    @uptime_command.subcommand(
        sub_cmd_name="list",
        sub_cmd_description="Liste toutes les alertes de maintenance configurées",
    )
    async def list_maintenance_alerts(self, ctx: SlashContext):
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return

        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, "Le module Uptime n'est pas activé sur ce serveur.")
            return

        guild_id = str(ctx.guild.id)

        if guild_id not in self.maintenance_monitors or not self.maintenance_monitors[guild_id]:
            await send_error(ctx, "Aucune alerte de maintenance configurée sur ce serveur.")
            return

        embed = Embed(title="📋 Alertes de maintenance configurées", color=Colors.INFO)

        for sensor_id, config_data in self.maintenance_monitors[guild_id].items():
            channel = self.bot.get_channel(config_data["channel_id"])
            sensor_info = await self._get_sensor_info(int(sensor_id))
            sensor_name = (
                sensor_info.get("name", f"ID {sensor_id}") if sensor_info else f"ID {sensor_id}"
            )
            mode = config_data.get("mode", "detailed")

            embed.add_field(
                name=f"Capteur: {sensor_name}",
                value=f"Canal: {channel.mention if channel else 'Canal introuvable'}\nMode: {mode}",
                inline=False,
            )

        await ctx.send(embed=embed)
