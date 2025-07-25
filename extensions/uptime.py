"""
This module provides a Discord bot extension that sends a status update to a server at a specific time
and monitors specific sensors for maintenance notifications.
"""

import os
import json

import aiohttp
from interactions import (
    Extension, 
    listen, 
    Task, 
    IntervalTrigger, 
    Client,
    slash_command,
    SlashContext,
    slash_option,
    OptionType,
    Embed,
    BaseChannel
)

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleUptime")

class Uptime(Extension):
    """
    A Discord bot extension that sends a status update to a server at a specific time
    and monitors specific sensors for maintenance notifications.
    """
    def __init__(self, bot):
        self.bot : Client = bot
        # Dictionnaire pour stocker les configurations de surveillance par serveur
        # Format: {guild_id: {sensor_id: {"channel_id": int, "last_status": str}}}
        self.maintenance_monitors = {}
        # Derniers états connus des capteurs pour éviter les notifications dupliquées
        self.sensor_states = {}

    @listen()
    async def on_startup(self):
        """
        Start background tasks.
        """
        await self.load_maintenance_monitors()
        self.send_status_update.start()
        self.check_sensor_maintenance.start()
        await self.send_status_update()

    @slash_command(
        name="setup_maintenance_alert",
        description="Configure les alertes de maintenance pour un capteur spécifique"
    )
    @slash_option(
        name="sensor_id",
        description="ID du capteur à surveiller",
        opt_type=OptionType.INTEGER,
        required=True
    )
    @slash_option(
        name="channel",
        description="Canal où envoyer les notifications",
        opt_type=OptionType.CHANNEL,
        required=True
    )
    async def setup_maintenance_alert(self, ctx: SlashContext, sensor_id: int, channel: BaseChannel):
        """
        Configure une alerte de maintenance pour un capteur spécifique dans un canal donné.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return
            
        guild_id = str(ctx.guild.id)
        
        # Vérifier que l'API Uptime Kuma est configurée
        if not config.get('uptimeKumaUrl') or not config.get('uptimeKumaApiKey'):
            await ctx.send("❌ Configuration Uptime Kuma manquante. Vérifiez l'URL et la clé API.", ephemeral=True)
            return

        # Vérifier si le capteur existe
        sensor_info = await self._get_sensor_info(sensor_id)
        if not sensor_info:
            await ctx.send(f"❌ Capteur avec l'ID {sensor_id} introuvable.", ephemeral=True)
            return

        # Initialiser la structure si nécessaire
        if guild_id not in self.maintenance_monitors:
            self.maintenance_monitors[guild_id] = {}

        # Configurer la surveillance
        self.maintenance_monitors[guild_id][str(sensor_id)] = {
            "channel_id": channel.id,
            "last_status": None
        }

        # Sauvegarder la configuration
        await self.save_maintenance_monitors()

        embed = Embed(
            title="✅ Alerte de maintenance configurée",
            description=f"Les notifications de maintenance pour le capteur **{sensor_info.get('name', f'ID {sensor_id}')}** seront envoyées dans {channel.mention}",
            color=0x00FF00
        )
        await ctx.send(embed=embed)

    @slash_command(
        name="remove_maintenance_alert",
        description="Supprime les alertes de maintenance pour un capteur"
    )
    @slash_option(
        name="sensor_id",
        description="ID du capteur à ne plus surveiller",
        opt_type=OptionType.INTEGER,
        required=True
    )
    async def remove_maintenance_alert(self, ctx: SlashContext, sensor_id: int):
        """
        Supprime la surveillance de maintenance pour un capteur spécifique.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return
            
        guild_id = str(ctx.guild.id)
        sensor_id_str = str(sensor_id)

        if (guild_id not in self.maintenance_monitors or 
            sensor_id_str not in self.maintenance_monitors[guild_id]):
            await ctx.send(f"❌ Aucune alerte configurée pour le capteur ID {sensor_id}.", ephemeral=True)
            return

        del self.maintenance_monitors[guild_id][sensor_id_str]
        
        # Nettoyer si plus de surveillance pour ce serveur
        if not self.maintenance_monitors[guild_id]:
            del self.maintenance_monitors[guild_id]

        # Sauvegarder la configuration
        await self.save_maintenance_monitors()

        await ctx.send(f"✅ Alerte de maintenance supprimée pour le capteur ID {sensor_id}.")

    @slash_command(
        name="list_maintenance_alerts",
        description="Liste toutes les alertes de maintenance configurées"
    )
    async def list_maintenance_alerts(self, ctx: SlashContext):
        """
        Liste toutes les alertes de maintenance configurées pour ce serveur.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return
            
        guild_id = str(ctx.guild.id)

        if guild_id not in self.maintenance_monitors or not self.maintenance_monitors[guild_id]:
            await ctx.send("❌ Aucune alerte de maintenance configurée sur ce serveur.", ephemeral=True)
            return

        embed = Embed(
            title="📋 Alertes de maintenance configurées",
            color=0x0099FF
        )

        for sensor_id, config_data in self.maintenance_monitors[guild_id].items():
            channel = self.bot.get_channel(config_data["channel_id"])
            sensor_info = await self._get_sensor_info(int(sensor_id))
            sensor_name = sensor_info.get('name', f'ID {sensor_id}') if sensor_info else f'ID {sensor_id}'
            
            embed.add_field(
                name=f"Capteur: {sensor_name}",
                value=f"Canal: {channel.mention if channel else 'Canal introuvable'}",
                inline=False
            )

        await ctx.send(embed=embed)

    async def _get_sensor_info(self, sensor_id: int):
        """
        Récupère les informations d'un capteur via l'API Uptime Kuma.
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Authorization': f'Bearer {config.get("uptimeKumaApiKey", "")}'
                }
                url = f"https://{config['uptimeKumaUrl']}/api/monitor/{sensor_id}"
                
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.warning(f"Erreur API pour capteur {sensor_id}: {response.status}")
                        return None
        except Exception as error:
            logger.error(f"Erreur lors de la récupération du capteur {sensor_id}: {error}")
            return None

    @Task.create(IntervalTrigger(seconds=60))
    async def check_sensor_maintenance(self):
        """
        Vérifie périodiquement l'état des capteurs surveillés pour détecter les maintenances.
        """
        if not config.get('uptimeKumaUrl') or not config.get('uptimeKumaApiKey'):
            return

        for guild_id, sensors in self.maintenance_monitors.items():
            for sensor_id, monitor_config in sensors.items():
                try:
                    sensor_info = await self._get_sensor_info(int(sensor_id))
                    if not sensor_info:
                        continue

                    current_status = sensor_info.get('status', 'unknown')
                    last_status = monitor_config.get('last_status')
                    
                    # Détecter les changements d'état significatifs
                    if last_status != current_status:
                        await self._send_maintenance_notification(
                            guild_id, sensor_id, sensor_info, current_status, last_status, monitor_config
                        )
                        # Mettre à jour le dernier état connu
                        self.maintenance_monitors[guild_id][sensor_id]['last_status'] = current_status
                        # Sauvegarder la configuration mise à jour
                        await self.save_maintenance_monitors()

                except Exception as error:
                    logger.error(f"Erreur lors de la vérification du capteur {sensor_id}: {error}")

    async def load_maintenance_monitors(self):
        """
        Charge les configurations de surveillance depuis le fichier JSON.
        """
        try:
            file_path = f"{config['misc']['dataFolder']}/uptime_maintenance_monitors.json"
            with open(file_path, "r", encoding="utf-8") as file:
                self.maintenance_monitors = json.load(file)
            logger.info(f"Configurations de surveillance chargées: {len(self.maintenance_monitors)} serveurs")
        except FileNotFoundError:
            logger.info("Aucun fichier de surveillance trouvé, démarrage avec une configuration vide")
            self.maintenance_monitors = {}
        except Exception as error:
            logger.error(f"Erreur lors du chargement des configurations: {error}")
            self.maintenance_monitors = {}

    async def save_maintenance_monitors(self):
        """
        Sauvegarde les configurations de surveillance dans le fichier JSON.
        """
        try:
            file_path = f"{config['misc']['dataFolder']}/uptime_maintenance_monitors.json"
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(self.maintenance_monitors, file, indent=4, ensure_ascii=False)
            logger.debug("Configurations de surveillance sauvegardées")
        except Exception as error:
            logger.error(f"Erreur lors de la sauvegarde des configurations: {error}")

    async def _send_maintenance_notification(self, guild_id: str, sensor_id: str, sensor_info: dict, 
                                           current_status: str, last_status: str, monitor_config: dict):
        """
        Envoie une notification de maintenance dans le canal configuré.
        """
        try:
            channel = self.bot.get_channel(monitor_config['channel_id'])
            if not channel:
                logger.warning(f"Canal {monitor_config['channel_id']} introuvable pour les notifications")
                return

            # Vérifier que le canal peut recevoir des messages
            if not hasattr(channel, 'send'):
                logger.warning(f"Canal {monitor_config['channel_id']} ne supporte pas l'envoi de messages")
                return

            sensor_name = sensor_info.get('name', f'ID {sensor_id}')
            
            # Déterminer le type de notification
            if current_status == 'maintenance':
                embed = Embed(
                    title="🔧 Maintenance en cours",
                    description=f"Le capteur **{sensor_name}** est actuellement en maintenance.",
                    color=0xFFA500
                )
            elif last_status == 'maintenance' and current_status in ['up', 'online']:
                embed = Embed(
                    title="✅ Fin de maintenance",
                    description=f"Le capteur **{sensor_name}** est de nouveau opérationnel.",
                    color=0x00FF00
                )
            elif current_status in ['down', 'offline']:
                embed = Embed(
                    title="❌ Capteur hors ligne",
                    description=f"Le capteur **{sensor_name}** est actuellement hors ligne.",
                    color=0xFF0000
                )
            elif current_status in ['up', 'online'] and last_status in ['down', 'offline']:
                embed = Embed(
                    title="✅ Capteur en ligne",
                    description=f"Le capteur **{sensor_name}** est de nouveau en ligne.",
                    color=0x00FF00
                )
            else:
                # Autres changements d'état
                embed = Embed(
                    title="ℹ️ Changement d'état",
                    description=f"Le capteur **{sensor_name}** a changé d'état: {last_status} → {current_status}",
                    color=0x0099FF
                )

            # Ajouter des informations supplémentaires
            embed.add_field(name="ID du capteur", value=sensor_id, inline=True)
            embed.add_field(name="État actuel", value=current_status, inline=True)
            if sensor_info.get('url'):
                embed.add_field(name="URL", value=sensor_info['url'], inline=False)

            # Utiliser getattr pour éviter les problèmes de types
            send_method = getattr(channel, 'send', None)
            if send_method:
                await send_method(embed=embed)
            else:
                logger.warning(f"Impossible d'envoyer un message dans le canal {channel}")

        except Exception as error:
            logger.error(f"Erreur lors de l'envoi de la notification: {error}")

    @Task.create(IntervalTrigger(seconds=55))
    async def send_status_update(self):
        """
        Perform status checks and gather information about your service/script's status.
        """
        async with aiohttp.ClientSession() as session:
            try:
                # Create the URL
                url = f"https://{config['uptimeKumaUrl']}/api/push/{config['uptimeKumaToken']}?status=up&msg=OK&ping={round(self.bot.latency * 1000, 1)}"

                # Send the status update
                async with session.get(url) as response:
                    response.raise_for_status()
                    logger.debug("Status update sent successfully.")
            except aiohttp.ClientError as error:
                logger.error("Error sending status update: %s", error)


def setup(bot):
    """Setup function for loading the extension."""
    Uptime(bot)
