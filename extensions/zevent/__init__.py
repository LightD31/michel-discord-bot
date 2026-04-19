"""Extension Discord pour le suivi en direct du Zevent.

The extension is a thin glue class that composes mixins for the data fetching
APIs, Twitch stream aggregation, embed rendering, the refresh task, and the
``/zevent_finish`` command.
"""

import os
from datetime import timedelta

from interactions import BaseChannel, Client, Extension, Message, listen
from twitchAPI.twitch import Twitch

from src.core import logging as logutil
from src.discord_ext.messages import fetch_or_create_persistent_message

from ._common import (
    CHANNEL_ID,
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
        self.last_data_cache: dict | None = None
        self.last_update_time = None
        self._streamer_cache: dict[str, str] = {}
        self._streamer_cache_time = None
        self.STREAMER_CACHE_TTL = timedelta(hours=24)
        self._planning_cache: list | None = None
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
            logger.info("Zevent extension initialized successfully")
            self.zevent.start()
            await self.zevent()
        except Exception as e:
            logger.error(f"Failed to initialize Zevent extension: {e}")
