"""Uptime Kuma Discord extension.

Polls Uptime Kuma's ``/metrics`` (API key) and mirrors monitor status changes
into Discord channels, and reports the bot's own liveness to a Kuma push
monitor. The Kuma calls live in ``src.integrations.uptime_kuma``; the
transition rules, embed texts and persistence in ``features.uptime``. This
package wires them to Discord: lifecycle here, ``/uptime`` in ``commands``,
alert posting in ``notifications`` and the periodic loops in ``tasks``.
"""

import asyncio

from interactions import Client, Extension, listen

from features.uptime import MonitorAlert, UptimeRepository
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.integrations.uptime_kuma import MonitorState

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
        # The last /metrics read; kept while Kuma is unreachable so names still resolve.
        self.monitors: dict[int, MonitorState] = {}
        self.kuma_ok = False
        # Polls, commands and the startup load all await while touching the alerts.
        self._state_lock = asyncio.Lock()
        self._poll_failing = False
        self._unset_logged = False
        self._push_failing = False

    @listen()
    async def on_startup(self):
        """Load the alerts, then start the loops with an immediate first run."""
        await self.load_alerts()
        if not self.poll_kuma.running:
            self.poll_kuma.start()
        if not self.push_status.running:
            self.push_status.start()
        await self.poll_kuma()
        await self.push_status()

    async def load_alerts(self) -> None:
        """Read every enabled guild's alerts; a guild that fails is retried by the next poll."""
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

    def current_status(self, monitor_id: int):
        """The monitor's status in the last /metrics read, ``None`` if it wasn't there."""
        state = self.monitors.get(monitor_id)
        return state.status if state else None

    def monitor_name(self, monitor_id: int) -> str:
        state = self.monitors.get(monitor_id)
        return state.name if state else f"ID {monitor_id}"


def setup(bot: Client) -> None:
    UptimeExtension(bot)
