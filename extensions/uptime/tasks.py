"""Scheduled tasks: push ping to Uptime Kuma + SocketIO health check / fallback poll."""

import os

import aiohttp
from interactions import IntervalTrigger, Task

from src.core import logging as logutil
from src.core.http import http_client

from ._common import config, has_kuma_credentials

logger = logutil.init_logger(os.path.basename(__file__))


class TasksMixin:
    """Periodic health-check loops for the Uptime extension."""

    @Task.create(IntervalTrigger(seconds=300))
    async def check_sensor_maintenance(self):
        """Watchdog: reconnect SocketIO if dropped, fall back to REST polling otherwise.

        Real-time updates flow through ``handle_monitor_update``; this loop only
        kicks in when the websocket is unavailable.
        """
        if not self.connected and self.sio:
            logger.warning("Connexion SocketIO perdue, tentative de reconnexion...")
            try:
                await self.connect_socketio()
            except Exception as error:
                logger.error(f"Erreur lors de la reconnexion SocketIO: {error}")

        if not self.connected:
            logger.info("SocketIO non connecté, utilisation de la vérification manuelle")
            await self._manual_sensor_check()

    async def _manual_sensor_check(self):
        """REST-based fallback when SocketIO is down."""
        if not has_kuma_credentials():
            return

        for guild_id, sensors in self.maintenance_monitors.items():
            for sensor_id, monitor_config in sensors.items():
                try:
                    sensor_info = await self._get_sensor_info(int(sensor_id))
                    if not sensor_info:
                        continue

                    current_status = sensor_info.get("status", "unknown")
                    last_status = monitor_config.get("last_status")

                    if last_status != current_status:
                        if "mode" not in monitor_config:
                            monitor_config["mode"] = "detailed"

                        await self._send_maintenance_notification(
                            guild_id,
                            sensor_id,
                            sensor_info,
                            current_status,
                            last_status,
                            monitor_config,
                        )
                        self.maintenance_monitors[guild_id][sensor_id]["last_status"] = (
                            current_status
                        )
                        await self.save_maintenance_monitors()

                except Exception as error:
                    logger.error(
                        f"Erreur lors de la vérification manuelle du capteur {sensor_id}: {error}"
                    )

    @Task.create(IntervalTrigger(seconds=55))
    async def send_status_update(self):
        """Push a heartbeat to the configured Uptime Kuma push endpoint."""
        try:
            url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}/api/push/{config['uptimeKuma']['uptimeKumaToken']}?status=up&msg=OK&ping={round(self.bot.latency * 1000, 1)}"
            session = await http_client.session()
            async with session.get(url) as response:
                response.raise_for_status()
        except aiohttp.ClientError as error:
            logger.error("Error sending status update: %s", error)
