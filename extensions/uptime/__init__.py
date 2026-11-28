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

from features.uptime import MonitorAlert, UptimeRepository, alerts_from_settings
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.integrations.uptime_kuma import MonitorState, MonitorStatus

from ._common import UPTIME
from .commands import CommandsMixin
from .notifications import NotificationsMixin
from .tasks import TasksMixin

logger = logutil.init_logger(__name__)


class UptimeExtension(Extension, CommandsMixin, NotificationsMixin, TasksMixin):
    """Discord extension that mirrors Uptime Kuma status changes into channels."""

    MODULE_KEYS = ("moduleUptime",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        # {guild_id: {monitor_id: the last settled status seen}}; the alerts
        # themselves are the module's settings, read live (P42).
        self.statuses: dict[str, dict[int, MonitorStatus | None]] = {}
        self._alerts_loaded = False
        # Each guild's last /metrics read, from its own Kuma (P37); kept while that
        # Kuma is unreachable so names still resolve.
        self.monitors: dict[str, dict[int, MonitorState]] = {}
        # The guilds whose last read worked.
        self.kuma_ok: set[str] = set()
        # Polls, commands and the startup load all await while touching the alerts.
        self._state_lock = asyncio.Lock()
        # Instances (base URLs) whose last read failed: logged once per outage.
        self._poll_failing: set[str] = set()
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
        """Read the last statuses seen in every enabled guild; one that fails is retried."""
        statuses: dict[str, dict[int, MonitorStatus | None]] = {}
        complete = True
        for guild_id in UPTIME.guild_ids():
            try:
                statuses[guild_id] = await UptimeRepository(guild_id).load_statuses()
            except DatabaseError as e:
                logger.error("Guild %s: could not load uptime statuses: %s", guild_id, e)
                complete = False
        async with self._state_lock:
            self.statuses = statuses
            self._alerts_loaded = complete
        logger.info("Uptime statuses loaded for %d guilds", len(statuses))

    def alerts_of(self, guild_id: str) -> dict[int, MonitorAlert]:
        """The guild's alerts as its settings say now, each with its last status seen."""
        return alerts_from_settings(UPTIME.get(guild_id), self.statuses.get(guild_id, {}))

    def current_status(self, guild_id: str, monitor_id: int) -> MonitorStatus | None:
        """The monitor's status in the guild's last /metrics read, ``None`` if it wasn't there."""
        state = self.monitors.get(guild_id, {}).get(monitor_id)
        return state.status if state else None

    def monitor_name(self, guild_id: str, monitor_id: int) -> str:
        state = self.monitors.get(guild_id, {}).get(monitor_id)
        return state.name if state else f"ID {monitor_id}"


def setup(bot: Client) -> None:
    UptimeExtension(bot)
