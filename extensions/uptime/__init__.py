"""Uptime Kuma Discord extension.

Composes the SocketIO client, monitor resolution, notification rendering,
admin slash commands, and scheduled tasks into a single ``UptimeExtension``.
"""

import os

from interactions import Client, Extension, listen

from features.uptime import UptimeRepository
from src.core import logging as logutil

from ._common import enabled_servers
from .commands import CommandsMixin
from .monitors import MonitorsMixin
from .notifications import NotificationsMixin
from .socketio_client import SocketIOMixin
from .tasks import TasksMixin

logger = logutil.init_logger(os.path.basename(__file__))


class UptimeExtension(
    Extension,
    SocketIOMixin,
    MonitorsMixin,
    NotificationsMixin,
    CommandsMixin,
    TasksMixin,
):
    """Discord extension that mirrors Uptime Kuma status updates into channels."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.sio = None
        self.connected = False
        # {guild_id: {sensor_id: {"channel_id": int, "last_status": str|None, "mode": str}}}
        self.maintenance_monitors: dict[str, dict] = {}
        self.sensor_states: dict = {}
        self.monitors_cache: dict = {}
        self._repository = UptimeRepository()

    @listen()
    async def on_startup(self):
        """Load persisted configs, connect SocketIO, then start the task loops."""
        await self.load_maintenance_monitors()
        await self.connect_socketio()
        self.send_status_update.start()
        self.check_sensor_maintenance.start()
        await self.send_status_update()

    async def load_maintenance_monitors(self):
        try:
            self.maintenance_monitors = await self._repository.load_all(list(enabled_servers))
            logger.info(
                f"Configurations de surveillance chargées: {len(self.maintenance_monitors)} serveurs"
            )
        except Exception as error:
            logger.error(f"Erreur lors du chargement des configurations: {error}")
            self.maintenance_monitors = {}

    async def save_maintenance_monitors(self):
        try:
            await self._repository.save_all(self.maintenance_monitors)
        except Exception as error:
            logger.error(f"Erreur lors de la sauvegarde des configurations: {error}")
