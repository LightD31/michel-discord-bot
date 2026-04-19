"""Build and send the maintenance / status-change Discord notifications."""

import os

from interactions import Embed, Timestamp

from features.uptime import normalize_status
from src import logutil
from src.helpers import Colors

logger = logutil.init_logger(os.path.basename(__file__))


class NotificationsMixin:
    """Render state-change embeds in the configured channel."""

    async def _send_maintenance_notification(
        self,
        guild_id: str,
        sensor_id: str,
        sensor_info: dict,
        current_status: str,
        last_status: str,
        monitor_config: dict,
    ):
        """Post a state-change embed.

        Only fires for events flagged ``important=True`` from Uptime Kuma.
        Uses the numeric status map (0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE) and
        ignores ``PENDING`` transitions entirely. Supports ``simple`` and
        ``detailed`` rendering modes.
        """
        try:
            is_important = sensor_info.get("important", False)
            if not is_important:
                logger.debug(
                    f"Événement non important ignoré pour moniteur {sensor_id}: status={current_status}"
                )
                return

            channel = self.bot.get_channel(monitor_config["channel_id"])
            if not channel:
                logger.warning(
                    f"Canal {monitor_config['channel_id']} introuvable pour les notifications"
                )
                return

            if not hasattr(channel, "send"):
                logger.warning(
                    f"Canal {monitor_config['channel_id']} ne supporte pas l'envoi de messages"
                )
                return

            sensor_name = sensor_info.get("name", f"ID {sensor_id}")
            notification_mode = monitor_config.get("mode", "detailed")

            current_status = normalize_status(current_status)
            last_status = normalize_status(last_status)

            if current_status == "PENDING":
                logger.debug(f"Statut PENDING ignoré pour moniteur {sensor_id}")
                return

            embed = None

            if current_status == "MAINTENANCE":
                if notification_mode == "simple":
                    embed = Embed(
                        title="🔧 Maintenance",
                        description=f"**{sensor_name}** est en maintenance",
                        color=Colors.ORANGE,
                    )
                else:
                    embed = Embed(
                        title="🔧 Maintenance en cours",
                        description=f"Le capteur **{sensor_name}** est actuellement en maintenance.",
                        color=Colors.ORANGE,
                    )
            elif current_status == "DOWN":
                if notification_mode == "simple":
                    embed = Embed(
                        title="❌ Hors ligne",
                        description=f"**{sensor_name}** est hors ligne",
                        color=Colors.ERROR,
                    )
                else:
                    embed = Embed(
                        title="❌ Capteur hors ligne",
                        description=f"Le capteur **{sensor_name}** est actuellement hors ligne.",
                        color=Colors.ERROR,
                    )
            elif current_status == "UP":
                if last_status in ("DOWN", "MAINTENANCE"):
                    if last_status == "MAINTENANCE":
                        if notification_mode == "simple":
                            embed = Embed(
                                title="✅ Maintenance terminée",
                                description=f"**{sensor_name}** opérationnel",
                                color=Colors.SUCCESS,
                            )
                        else:
                            embed = Embed(
                                title="✅ Fin de maintenance",
                                description=f"Le capteur **{sensor_name}** est de nouveau opérationnel après maintenance.",
                                color=Colors.SUCCESS,
                            )
                    else:
                        if notification_mode == "simple":
                            embed = Embed(
                                title="✅ Rétabli",
                                description=f"**{sensor_name}** est en ligne",
                                color=Colors.SUCCESS,
                            )
                        else:
                            embed = Embed(
                                title="✅ Capteur rétabli",
                                description=f"Le capteur **{sensor_name}** est de nouveau en ligne.",
                                color=Colors.SUCCESS,
                            )
                else:
                    logger.debug(
                        f"Changement d'état UP non significatif ignoré pour moniteur {sensor_id}"
                    )
                    return

            if not embed:
                logger.debug(
                    f"Aucun embed créé pour moniteur {sensor_id}: {last_status} → {current_status}"
                )
                return

            if notification_mode == "detailed":
                embed.add_field(name="ID du capteur", value=sensor_id, inline=True)
                embed.add_field(name="État actuel", value=current_status, inline=True)

                if last_status and last_status != current_status:
                    embed.add_field(name="État précédent", value=last_status, inline=True)

                if sensor_info.get("url"):
                    embed.add_field(name="URL", value=sensor_info["url"], inline=False)
                if sensor_info.get("msg"):
                    embed.add_field(name="Message", value=sensor_info["msg"], inline=False)
                if sensor_info.get("ping") is not None:
                    embed.add_field(name="Ping", value=f"{sensor_info['ping']} ms", inline=True)

                embed.timestamp = Timestamp.now()
            else:
                embed.add_field(name="Statut", value=current_status, inline=True)

            send_method = getattr(channel, "send", None)
            if send_method:
                await send_method(embed=embed)
                logger.info(
                    f"Notification envoyée pour moniteur {sensor_id}: {last_status} → {current_status} (mode: {notification_mode})"
                )
            else:
                logger.warning(f"Impossible d'envoyer un message dans le canal {channel}")

        except Exception as error:
            logger.error(f"Erreur lors de l'envoi de la notification: {error}")
