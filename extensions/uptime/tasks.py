"""Periodic loops: the /metrics poll and the bot's own push heartbeat."""

import math

from interactions import IntervalTrigger, Task

from src.core import log_context
from src.core import logging as logutil
from src.integrations.uptime_kuma import (
    UptimeKumaError,
    UptimeKumaSettings,
    fetch_monitor_states,
    push_heartbeat,
)

from ._common import POLL_SECONDS, UPTIME

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
        # Each guild reads its own Kuma (P37); guilds sharing one instance and key share a read.
        instances: dict[UptimeKumaSettings, list[str]] = {}
        for guild_id, raw in UPTIME.items():
            with log_context.scope(guild_id=guild_id):
                settings = UptimeKumaSettings.from_section(raw)
                if settings.metrics_configured:
                    instances.setdefault(settings, []).append(guild_id)
                else:
                    self.kuma_ok.discard(guild_id)  # none of its own: the health card says so
        for settings, guild_ids in instances.items():
            try:
                states = await fetch_monitor_states(settings)
            except UptimeKumaError as e:
                # Once per outage: an unreachable Kuma would otherwise log every poll.
                if settings.base_url not in self._poll_failing:
                    logger.warning("Uptime Kuma %s: %s; retrying quietly", settings.base_url, e)
                self._poll_failing.add(settings.base_url)
                self.kuma_ok.difference_update(guild_ids)
                continue
            if settings.base_url in self._poll_failing:
                logger.info("Uptime Kuma %s: /metrics readable again", settings.base_url)
            self._poll_failing.discard(settings.base_url)
            for guild_id in guild_ids:
                with log_context.scope(guild_id=guild_id):
                    self.kuma_ok.add(guild_id)
                    self.monitors[guild_id] = states
                    await self.apply_states(guild_id, states)

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
