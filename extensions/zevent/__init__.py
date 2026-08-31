"""Extension Discord pour le suivi en direct du Zevent.

The extension is a thin glue class that composes mixins for the data fetching
APIs, Twitch stream aggregation, embed rendering, the refresh task, and the
``/zevent_finish`` command.
"""

import asyncio
import os
from datetime import timedelta

from interactions import BaseChannel, Client, Extension, Message, listen
from twitchAPI.twitch import Twitch

from features.zevent.models import Participant, Show
from src.core import logging as logutil
from src.discord_ext.messages import fetch_or_create_persistent_message

from ._common import (
    CHANNEL_ID,
    EVENT_NAME,
    GUILD_ID,
    MESSAGE_ID,
    PIN_MESSAGE,
    config,
)
from .api import ApiMixin
from .commands import CommandsMixin
from .embeds import EmbedsMixin
from .streams import StreamsMixin
from .tasks import TasksMixin

logger = logutil.init_logger(os.path.basename(__file__))


class Zevent(Extension, ApiMixin, StreamsMixin, EmbedsMixin, TasksMixin, CommandsMixin):
    """Live Zevent tracker — refreshes a pinned message on a fixed interval."""

    def __init__(self, client: Client):
        self.client: Client = client
        self.channel: BaseChannel | None = None
        self.message: Message | None = None
        self.twitch: Twitch | None = None
        self.last_milestone = 0
        self._milestone_lock = asyncio.Lock()
        self.last_data_cache: dict | None = None
        self.last_update_time = None
        self._stats_event: dict | None = None
        self._stats_event_time = None
        self.STATS_EVENT_CACHE_TTL = timedelta(hours=6)
        self._event_title: str = EVENT_NAME or "Zevent"
        self._participant_cache: list[Participant] = []
        self._participant_cache_time = None
        self._location_index: dict[str, str] = {}
        self.PARTICIPANT_CACHE_TTL = timedelta(minutes=10)
        self._planning_cache: list[Show] | None = None
        self._planning_cache_time = None
        self.PLANNING_CACHE_TTL = timedelta(minutes=15)

    @listen()
    async def on_startup(self):
        """Resolve/create the pinned message, auth Twitch, and kick off the refresh."""
        try:
            self.message = await fetch_or_create_persistent_message(
                self.client,
                channel_id=CHANNEL_ID,
                message_id=MESSAGE_ID,
                module_name="moduleZevent",
                message_id_key="zeventMessageId",
                guild_id=GUILD_ID,
                initial_content="Initialisation Zevent…",
                pin=PIN_MESSAGE,
                logger=logger,
            )
            if self.message is not None:
                self.channel = self.message.channel

            self.twitch = await Twitch(
                config["twitch"]["twitchClientId"],
                config["twitch"]["twitchClientSecret"],
            )
            # Resolve the tracked edition up front so the embed title is right
            # on the very first render, even if zevent.fr is unreachable.
            await self._ensure_stats_event()
            logger.info("Zevent extension initialized successfully")
            self.zevent.start()
            await self.zevent()
        except Exception as e:
            logger.error(f"Failed to initialize Zevent extension: {e}")
