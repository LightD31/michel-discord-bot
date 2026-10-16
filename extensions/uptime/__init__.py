"""Uptime Kuma Discord extension.

Mirrors Uptime Kuma status changes into Discord channels and reports the bot's
own liveness to a Kuma push monitor. The Socket.IO client lives in
``src.integrations.uptime_kuma``; the transition rules, embed texts and
persistence in ``features.uptime``. This package wires them to Discord:
lifecycle here, ``/uptime`` in ``commands``, alert posting in
``notifications`` and the periodic loops in ``tasks``.
"""

import asyncio

from interactions import Client, Extension, listen

from features.uptime import MonitorAlert, UptimeRepository
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.integrations.uptime_kuma import UptimeKumaClient

from ._common import enabled_servers
from .commands import CommandsMixin
from .notifications import NotificationsMixin
from .tasks import TasksMixin

logger = logutil.init_logger(__name__)


class UptimeExtension(Extension, CommandsMixin, NotificationsMixin, TasksMixin):
    """Discord extension that mirrors Uptime Kuma status changes into channels."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        # {guild_id: {monitor_id: alert}}, guilds with the module enabled only.
        self.alerts: dict[str, dict[int, MonitorAlert]] = {}
        self._alerts_loaded = False
        # Socket.IO handlers run concurrently; transitions must not interleave.
        self._state_lock = asyncio.Lock()
        self._push_failing = False
        self.kuma = UptimeKumaClient(on_heartbeat=self.handle_heartbeat)

    @listen()
    async def on_startup(self):
        """Load the alerts, open the Kuma link, then start the loops."""
        await self.load_alerts()
        self.kuma.start()
        if not self.push_status.running:
            self.push_status.start()
        if not self.kuma_watchdog.running:
            self.kuma_watchdog.start()
        await self.push_status()

    async def async_drop(self):
        """Dashboard unload/reload: close the Socket.IO link before a new instance opens one."""
        await self.kuma.close()

    async def load_alerts(self) -> None:
        """Read every enabled guild's alerts; a guild that fails is retried by the watchdog."""
        alerts: dict[str, dict[int, MonitorAlert]] = {}
        complete = True
        for guild_id in enabled_servers:
            try:
                alerts[guild_id] = await UptimeRepository(guild_id).load()
            except DatabaseError as e:
                logger.error("Guild %s: could not load uptime alerts: %s", guild_id, e)
                complete = False
        async with self._state_lock:
            self.alerts = alerts
            self._alerts_loaded = complete
        logger.info(
            "Uptime alerts loaded: %d across %d guilds",
            sum(len(guild_alerts) for guild_alerts in alerts.values()),
            len(alerts),
        )


def setup(bot: Client) -> None:
    UptimeExtension(bot)
