"""Periodic loops: the /metrics poll and the bot's own push heartbeat."""

import math

from interactions import IntervalTrigger, Task

from src.core import logging as logutil
from src.integrations.uptime_kuma import (
    UptimeKumaError,
    UptimeKumaSettings,
    fetch_monitor_states,
    push_heartbeat,
)

from ._common import POLL_SECONDS

logger = logutil.init_logger(__name__)


class TasksMixin:
    """Kuma polling and push heartbeat for the Uptime extension."""

    @Task.create(IntervalTrigger(seconds=POLL_SECONDS))
    async def poll_kuma(self):
        """Read every monitor's status and post the changes.

        Kuma refreshes ``/metrics`` on each check, so a change shows up here
        within one poll. One read uses one of Kuma's 60 API requests a minute.
        """
        if not self._alerts_loaded:
            await self.load_alerts()
        settings = UptimeKumaSettings.load()
        if not settings.metrics_configured:
            if not self._unset_logged:
                logger.info("Uptime Kuma: URL or API key (uptimeKumaApiKey) unset, alerts off")
                self._unset_logged = True
            self.kuma_ok = False
            return
        self._unset_logged = False
        try:
            states = await fetch_monitor_states(settings)
        except UptimeKumaError as e:
            # Once per outage: an unreachable Kuma would otherwise log every poll.
            if not self._poll_failing:
                logger.warning("Uptime Kuma: %s; retrying quietly", e)
            self._poll_failing = True
            self.kuma_ok = False
            return
        if self._poll_failing:
            logger.info("Uptime Kuma: /metrics readable again")
        self._poll_failing = False
        self.kuma_ok = True
        self.monitors = states
        await self.apply_states(states)

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
            if not self._push_failing:
                logger.warning("Uptime Kuma push failed, retrying quietly: %s", e)
            self._push_failing = True
            return
        if self._push_failing:
            logger.info("Uptime Kuma push working again")
        self._push_failing = False
