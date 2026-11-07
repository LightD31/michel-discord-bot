"""Which new uploads a guild is told about, and what the notification says.

Pure functions over the YouTube Data API's ``videos`` item and the guild's
``moduleYoutube`` config; ``extensions/youtube.py`` does the API calls.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

import isodate

VideoKind = Literal["live", "short", "vod"]

DEFAULT_SHORT_MAX_SECONDS = 90
DEFAULT_TEMPLATE = "https://www.youtube.com/watch?v={video_id}"

# Per-channel toggle for each kind, and whether it's on when the toggle is unset.
_TOGGLES: dict[VideoKind, tuple[str, bool]] = {
    "live": ("live", False),
    "short": ("shorts", False),
    "vod": ("vod", True),
}


def short_max_seconds(guild_config: dict[str, Any]) -> int:
    """The guild's Short threshold in seconds: at least 1, 90 when unset or invalid."""
    try:
        value = int(guild_config.get("youtubeShortMaxSeconds", DEFAULT_SHORT_MAX_SECONDS))
    except (TypeError, ValueError):
        value = DEFAULT_SHORT_MAX_SECONDS
    return max(1, value)


def classify(item: dict[str, Any], short_max: int) -> VideoKind:
    """A live (or upcoming) broadcast, a Short (no longer than *short_max* s), or a VOD."""
    if item["snippet"]["liveBroadcastContent"] != "none":
        return "live"
    duration = isodate.parse_duration(item["contentDetails"]["duration"])
    return "short" if duration <= timedelta(seconds=short_max) else "vod"


def wanted(kind: VideoKind, channel_config: dict[str, Any]) -> bool:
    """Whether the channel's toggles ask for this kind of video (VODs only by default)."""
    key, default = _TOGGLES[kind]
    return bool(channel_config.get(key, default))


def render_notification(template: str, *, video_id: str, handle: str, label: str) -> str:
    """Fill the guild's template; a broken template falls back to the plain video link."""
    try:
        return template.format(video_id=video_id, handle=handle, label=label)
    except (KeyError, IndexError, ValueError, AttributeError):
        return DEFAULT_TEMPLATE.format(video_id=video_id)


__all__ = [
    "DEFAULT_SHORT_MAX_SECONDS",
    "DEFAULT_TEMPLATE",
    "VideoKind",
    "classify",
    "render_notification",
    "short_max_seconds",
    "wanted",
]
