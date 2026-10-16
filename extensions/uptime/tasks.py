"""Periodic loops: the bot's own push heartbeat and the Kuma link watchdog."""

import math

from interactions import IntervalTrigger, Task

from src.core import logging as logutil
from src.integrations.uptime_kuma import UptimeKumaError, UptimeKumaSettings, push_heartbeat

logger = logutil.init_logger(__name__)


class TasksMixin:
    """Push heartbeat and recovery loops for the Uptime extension."""

    @Task.create(IntervalTrigger(seconds=55))
    async def push_status(self):
        """Report the bot as up to its Kuma push monitor (``uptimeKumaToken``)."""
        settings = UptimeKumaSettings.load()
        if not settings.push_configured:
            return
        # None (gateway not connected yet) or inf (no heartbeat acked yet): push without a ping.
        latency = self.bot.latency
        ping_ms = (
            latency * 1000 if isinstance(latency, int | float) and math.isfinite(latency) else None
        )
        try:
            await push_heartbeat(settings, ping_ms)
        except UptimeKumaError as e:
            # Once per outage: Kuma being down would otherwise log every 55 s.
            if not self._push_failing:
                logger.warning("Uptime Kuma push failed, retrying quietly: %s", e)
            self._push_failing = True
            return
        if self._push_failing:
            logger.info("Uptime Kuma push working again")
        self._push_failing = False

    @Task.create(IntervalTrigger(minutes=5))
    async def kuma_watchdog(self):
        """Retry what can't heal itself: a refused login and alerts that failed to load.

        Reconnecting a dropped link is python-socketio's job.
        """
        if self.kuma.needs_login:
            await self.kuma.login()
        if not self._alerts_loaded:
            await self.load_alerts()
