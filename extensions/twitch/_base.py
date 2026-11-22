"""What the Twitch mixins share: the extension's state, and one another's methods.

Every mixin derives from :class:`TwitchBase`, so mypy checks each against the
state ``TwitchExtension.__init__`` sets up and the methods its siblings
provide. The attributes are annotations only, and the siblings' methods are
declared under ``TYPE_CHECKING``, so the real methods (and the MRO) are those
of the mixins; :meth:`TwitchBase._api` is the one method defined here.
"""

from __future__ import annotations

from datetime import tzinfo
from typing import TYPE_CHECKING, Any

from interactions import Client, Embed, Task
from twitchAPI.object.api import Stream
from twitchAPI.object.eventsub import ChannelUpdateEvent
from twitchAPI.twitch import Twitch

from src.core.errors import IntegrationError
from src.integrations.twitch_eventsub import BoundedEventSubWebsocket

from ._common import StreamerInfo


class TwitchBase:
    bot: Client
    config: dict[str, Any]
    module_config: dict[str, dict]
    enabled_servers: list[str]
    client_id: str
    client_secret: str
    streamers: dict[str, StreamerInfo]
    # Both set by the EventSub start (eventsub.py), ``None`` until then.
    twitch: Twitch | None
    eventsub: BoundedEventSubWebsocket | None
    stop: bool
    timezone: tzinfo
    update: Task

    def _api(self) -> Twitch:
        """The Twitch client, once the EventSub start has created it."""
        if self.twitch is None:
            raise IntegrationError("the Twitch client is not started yet")
        return self.twitch

    if TYPE_CHECKING:
        # __init__.py
        def get_streamer_by_user_id(self, user_id: str) -> list[StreamerInfo]: ...
        async def _save_streamer_state(self, streamer_id: str) -> None: ...

        # eventsub.py
        async def get_stream_data(self, user_id: str) -> Stream | None: ...

        # notifications.py
        @staticmethod
        def get_display_value(value: str | None, fallback: str = "Non renseigné") -> str: ...
        async def create_notification_embed(
            self, guild_id: int, title: str, description: str
        ) -> Embed: ...
        async def create_stream_embed(
            self,
            stream: Stream | None,
            user_id: str,
            offline: bool = False,
            data: ChannelUpdateEvent | None = None,
            guild_id: int | None = None,
        ) -> Embed: ...
        async def get_stream_info(
            self,
            stream: Stream | None,
            user_id: str,
            offline: bool,
            data: ChannelUpdateEvent | None = None,
        ) -> tuple: ...
        async def send_stream_start_notification(
            self, streamer: StreamerInfo, broadcaster_name: str, stream: Stream | None
        ) -> None: ...
        async def send_stream_end_notification(
            self, streamer: StreamerInfo, broadcaster_name: str
        ) -> None: ...

        # schedule.py
        async def edit_message(
            self,
            streamer: StreamerInfo,
            offline: bool = False,
            data: ChannelUpdateEvent | None = None,
        ) -> None: ...
