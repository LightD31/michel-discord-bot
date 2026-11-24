"""Shared dataclass and small utilities for the Twitch extension package.

Kept separate from ``__init__.py`` so submodules (notifications, eventsub,
schedule, emotes) can import these without triggering an import cycle through
the package root.
"""

from datetime import UTC, datetime
from typing import Any, overload

from interactions import ScheduledEvent
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.config import ModuleConfig
from src.webui.schemas import (
    ModuleHelp,
    SchemaBase,
    check_template,
    enabled_field,
    register_module,
    ui,
)

# The longest message a streamer's notification carries above its embed.
MAX_LIVE_TEMPLATE_LENGTH = 500


class StreamerEntry(BaseModel):
    """One streamer of the ``streamermap`` widget (keyed by Twitch login).

    Written by the SPA, and by ``/twitch-planning`` for the planning keys.
    """

    model_config = ConfigDict(extra="forbid")

    twitchPlanningChannelId: str | None = None
    twitchPlanningPinMessage: bool = False
    twitchNotificationChannelId: str | None = None
    notifyStreamStart: bool = False
    notifyStreamUpdate: bool = False
    notifyStreamEnd: bool = False
    notifyEmoteChanges: bool = False
    manageDiscordEvents: bool = False
    # The role the "live started" notification pings, which its 🔔
    # button hands out, and the text above it.
    twitchMentionRoleId: str | None = None
    twitchNotificationTemplate: str = Field("", max_length=MAX_LIVE_TEMPLATE_LENGTH)

    @field_validator("twitchNotificationTemplate")
    @classmethod
    def _plain_placeholders(cls, template: str) -> str:
        return check_template(template)


@register_module("moduleTwitch")
class TwitchConfig(SchemaBase):
    __label__ = "Twitch"
    __description__ = "Notifications de live et planning des streamers."
    __icon__ = "📺"
    __category__ = "Médias & Streaming"
    __help__ = ModuleHelp(
        "Les lives et le planning des streamers suivis.",
        (),
        "Un live qui commence est annoncé, avec un bouton « 🔔 M'avertir des lives » "
        "qui donne le rôle mentionné ; le planning de la semaine se met à jour.",
    )

    enabled: bool = enabled_field()
    twitchStreamerList: dict[str, StreamerEntry] = ui(
        "Streamers suivis",
        "streamermap",
        required=True,
        description=(
            "Liste des streamers Twitch à suivre. "
            "Chaque streamer a ses propres salons et préférences de notifications. "
            "Le **rôle à mentionner** est notifié à chaque début de live, et le bouton "
            "« 🔔 M'avertir des lives » le donne à qui le demande. Dans le message, "
            "`{streamer}`, `{titre}`, `{jeu}` et `{lien}` sont remplacés."
        ),
    )


TWITCH = ModuleConfig("moduleTwitch")


@overload
def ensure_utc(dt: datetime) -> datetime: ...
@overload
def ensure_utc(dt: None) -> None: ...
def ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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
        self.planning_pin = bool(config.get("twitchPlanningPinMessage", False))
        self.notification_channel_id = int(config.get("twitchNotificationChannelId", 0))
        # Per-streamer notification settings
        self.notify_stream_start = bool(config.get("notifyStreamStart", False))
        self.notify_stream_update = bool(config.get("notifyStreamUpdate", False))
        self.notify_stream_end = bool(config.get("notifyStreamEnd", False))
        self.notify_emote_changes = bool(config.get("notifyEmoteChanges", False))
        self.manage_discord_events = bool(config.get("manageDiscordEvents", False))
        role = str(config.get("twitchMentionRoleId") or "").strip()
        self.mention_role_id: str | None = role if role.isdigit() else None
        self.notification_template = str(config.get("twitchNotificationTemplate") or "")
        # The notification channel, resolved at startup; the live's Discord event.
        self.notif_channel: Any = None
        self.scheduled_event: ScheduledEvent | None = None
        # Stream session info (stored when stream starts)
        self.stream_start_time: datetime | None = None
        self.stream_title: str | None = None
        self.stream_id: str | None = None
        # Last notified title and category (to avoid duplicate notifications)
        self.last_notified_title: str | None = None
        self.last_notified_category: str | None = None
        # Ordered list of categories played during the current live session
        self.stream_categories: list[str] = []

    @property
    def planning_slot(self) -> str:
        """The streamer's planning message in ``bot_messages`` (src.core.message_refs)."""
        return f"twitch:planning:{self.streamer_id}"
