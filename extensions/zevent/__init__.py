"""Extension Discord pour le suivi en direct du Zevent.

The extension is a thin glue class that composes mixins for the data fetching
APIs, Twitch stream aggregation, embed rendering, the refresh task, and the
``/zevent_finish`` command.
"""

import os
from datetime import timedelta

from interactions import Client, Extension, listen
from twitchAPI.twitch import Twitch

from features.zevent.backoff import RetryGate
from features.zevent.history import DonationCurve
from features.zevent.models import Participant, Show
from features.zevent.velocity import DonationVelocity
from src.core import logging as logutil
from src.core.config import global_config
from src.core.logging import log_failure
from src.discord_ext.messages import GuildMessages

from ._common import (
    EVENT_END_OVERRIDE,
    EVENT_NAME,
    EVENT_START_OVERRIDE,
    FALLBACK_EVENT_START,
    FALLBACK_MAIN_EVENT_START,
    MAIN_EVENT_START_OVERRIDE,
    ZEVENT,
    ZeventGuild,
)
from .api import ApiMixin
from .commands import CommandsMixin
from .discord_event import DiscordEventMixin
from .embeds import EmbedsMixin
from .streams import StreamsMixin
from .tasks import TasksMixin

logger = logutil.init_logger(__name__)


class Zevent(
    Extension,
    ApiMixin,
    StreamsMixin,
    EmbedsMixin,
    DiscordEventMixin,
    TasksMixin,
    CommandsMixin,
):
    """Live Zevent tracker — refreshes each enabled guild's message on a fixed interval.

    The edition is tracked once for the whole bot (global section ``zevent``);
    each guild where the module is on gets the same embeds in its own channel,
    its own milestone and record announcements, and optionally its own Discord
    scheduled event. Its settings are read at each refresh.
    """

    MODULE_KEYS = ("moduleZevent",)

    def __init__(self, client: Client):
        self.client: Client = client
        self.twitch: Twitch | None = None
        self.messages = GuildMessages(
            client,
            slot="zevent",
            label="Suivi Zevent",
            channel_key="zeventChannelId",
            pin_key="zeventPinMessage",
            initial_content="Initialisation Zevent…",
            logger=logger,
        )
        self.guilds: dict[str, ZeventGuild] = {}
        self.last_data_cache: dict | None = None
        self.last_update_time = None
        self._stats_event: dict | None = None
        # The stats API is a community project on modest hardware: refresh it
        # on generous intervals, and let the gates widen those on failure
        # rather than retrying every 30 s refresh.
        self._event_gate = RetryGate(timedelta(hours=6))
        self._event_title: str = EVENT_NAME or "Zevent"
        # Replaced by the stats API's schedule on the first resolve unless the
        # operator pinned them in the dashboard.
        self._event_start = EVENT_START_OVERRIDE or FALLBACK_EVENT_START
        self._main_event_start = MAIN_EVENT_START_OVERRIDE or FALLBACK_MAIN_EVENT_START
        # Only the Discord scheduled event needs an end; unset, the planner
        # falls back on a duration past the start.
        self._event_end = EVENT_END_OVERRIDE
        self._last_event_total: float | None = None
        self._covers: dict[str, bytes | None] = {}
        # Twitch logins seen live on the last poll, shared with the embeds so
        # the goals ranking uses fresh presence rather than the cached flag.
        self._live_logins: set[str] = set()
        self._stream_viewers: dict[str, int] = {}
        self._stream_snapshot_time = None
        self._participant_cache: list[Participant] = []
        self._location_index: dict[str, str] = {}
        self._participant_gate = RetryGate(timedelta(minutes=10))
        # Measured from the zevent.fr payload the refresh loop already pulls,
        # so a goal being pushed over is visible without touching the
        # community stats API any harder.
        self._stats_events: list[dict] = []
        # A finished edition's curve never changes; refresh it rarely, and let
        # the gate widen that further if the cache is unreachable.
        self._reference_curve: DonationCurve | None = None
        self._reference_gate = RetryGate(timedelta(hours=12))
        self._velocity = DonationVelocity()
        self._planning_cache: list[Show] | None = None
        # The schedule barely moves during the event; 15 minutes was needless.
        self._planning_gate = RetryGate(timedelta(minutes=30))

    @listen()
    async def on_startup(self):
        """Auth Twitch, resolve the edition, and kick off the refresh.

        Each guild's message, markers and scheduled event are picked up by the
        refresh itself, the first time it serves that guild.
        """
        try:
            twitch = global_config().get("twitch") or {}
            self.twitch = await Twitch(twitch["twitchClientId"], twitch["twitchClientSecret"])
            # Resolve the tracked edition up front so the embed title is right
            # on the very first render, even if zevent.fr is unreachable.
            await self._ensure_stats_event()
            logger.info("Zevent extension initialized successfully")
            if not self.zevent.running:
                self.zevent.start()
            await self.zevent()
        except Exception as e:
            log_failure(logger, f"Failed to initialize Zevent extension: {e}")

    async def _guilds(self) -> list[tuple[ZeventGuild, dict]]:
        """``(state, settings)`` for each guild where the module is on, now.

        A guild seen for the first time gets its message and has its markers
        picked back up (stored per edition, so after the edition is resolved);
        its scheduled event is picked back up the first time it asks for one.
        """
        guilds = ZEVENT.items()
        enabled = [guild_id for guild_id, _ in guilds]
        self.messages.keep_only(enabled)
        for guild_id in [g for g in self.guilds if g not in enabled]:
            del self.guilds[guild_id]
        out = []
        for guild_id, settings in guilds:
            guild = self.guilds.setdefault(guild_id, ZeventGuild(guild_id))
            guild.message = await self.messages.get(guild_id, settings)
            if not guild.markers_loaded:
                guild.markers_loaded = True
                await self.load_milestone_marker(guild)
                await self.load_record_marker(guild)
            if not guild.event_recovered and settings.get("zeventManageDiscordEvent"):
                guild.event_recovered = True
                # Re-attach the scheduled event this bot created before the
                # restart so the refresh edits it instead of creating a duplicate.
                await self.recover_scheduled_event(guild, settings)
            out.append((guild, settings))
        return out
