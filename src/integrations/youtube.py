"""The YouTube Data API, with the key from the global ``youtube`` section (no Discord).

Shared by the notifications extension and the dashboard's "Vérifier la
connexion" (P16). The key travels in the ``X-Goog-Api-Key`` header, never in
the URL, so it can't end up in a logged URL or an error message.
"""

from __future__ import annotations

from typing import Any

from src.core import logging as logutil
from src.core.config import global_config
from src.core.http import fetch

logger = logutil.init_logger(__name__)


def _setting(name: str) -> str:
    """A field of the global section ``youtube``, read at each call."""
    return str((global_config().get("youtube") or {}).get(name) or "").strip()


def api_key() -> str:
    """The YouTube Data API key."""
    return _setting("youtubeApiKey")


def api_url() -> str:
    """The YouTube Data API base URL, without a trailing slash."""
    return _setting("youtubeApiUrl").rstrip("/")


async def youtube_api(endpoint: str, **params: str | int) -> dict[str, Any]:
    """GET a YouTube Data API endpoint (the key in a header)."""
    data = await fetch(
        f"{api_url()}/{endpoint}",
        return_type="json",
        params=params,
        headers={"X-Goog-Api-Key": api_key()},
    )
    logger.debug(data)
    return data


async def uploads_playlist(handle: str) -> str:
    """The uploads playlist of the channel ``@handle`` (raises when there is none)."""
    data = await youtube_api("channels", part="contentDetails", forHandle=handle)
    return data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


async def latest_upload(uploads: str) -> dict[str, Any]:
    """The snippet of the playlist's latest item (its ``title``, ``resourceId``…)."""
    data = await youtube_api("playlistItems", part="snippet", maxResults=1, playlistId=uploads)
    return data["items"][0]["snippet"]


__all__ = ["api_key", "api_url", "latest_upload", "uploads_playlist", "youtube_api"]
