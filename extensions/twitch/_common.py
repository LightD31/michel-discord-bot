"""Shared dataclass and small utilities for the Twitch extension package.

Kept separate from ``__init__.py`` so submodules (notifications, eventsub,
schedule, emotes) can import these without triggering an import cycle through
the package root.
"""

from datetime import datetime
from typing import Any

import pytz

from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleTwitch")
class TwitchConfig(SchemaBase):
    __label__ = "Twitch"
    __description__ = "Notifications de live et planning des streamers."
    __icon__ = "📺"
    __category__ = "Médias & Streaming"

    enabled: bool = enabled_field()
    twitchStreamerList: dict[str, Any] = ui(
        "Streamers suivis",
        "streamermap",
        required=True,
        description=(
            "Liste des streamers Twitch à suivre. "
            "Chaque streamer a ses propres salons et préférences de notifications."
        ),
    )


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return pytz.UTC.localize(dt)
    return dt.astimezone(pytz.UTC)


class StreamerInfo:
    """Per-streamer, per-guild configuration + live session state.

    Some fields (``stream_*``, ``last_notified_*``) are hydrated from MongoDB at
    startup and persisted again after each EventSub callback.
    """

    def __init__(self, guild_id: int, streamer_id: str, config: dict):
        self.guild_id = guild_id
        self.streamer_id = streamer_id
        self.user_id: str | None = None
        self.planning_channel_id = int(config.get("twitchPlanningChannelId", 0))
        self.planning_message_id = int(config.get("twitchPlanningMessageId", 0))
        self.planning_pin = bool(config.get("twitchPlanningPinMessage", False))
        self.notification_channel_id = int(config.get("twitchNotificationChannelId", 0))
        # Per-streamer notification settings
        self.notify_stream_start = bool(config.get("notifyStreamStart", False))
        self.notify_stream_update = bool(config.get("notifyStreamUpdate", False))
        self.notify_stream_end = bool(config.get("notifyStreamEnd", False))
        self.notify_emote_changes = bool(config.get("notifyEmoteChanges", False))
        self.manage_discord_events = bool(config.get("manageDiscordEvents", False))
        self.channel = None
        self.message = None
        self.notif_channel = None
        self.scheduled_event = None
        # Stream session info (stored when stream starts)
        self.stream_start_time: datetime | None = None
        self.stream_title: str | None = None
        self.stream_id: str | None = None
        # Last notified title and category (to avoid duplicate notifications)
        self.last_notified_title: str | None = None
        self.last_notified_category: str | None = None
        # Ordered list of categories played during the current live session
        self.stream_categories: list[str] = []
