"""The YouTube Data API, with each server's own key (P37; no Discord).

The key and the API's URL are ``moduleYoutube`` settings: a server spends its
own quota. Shared by the notifications extension and the dashboard's
"Vérifier la connexion" (P16), which pass the server's settings. The key travels in the ``X-Goog-Api-Key`` header, never in
the URL, so it can't end up in a logged URL or an error message.
"""

from __future__ import annotations

from typing import Any

from src.core import logging as logutil
from src.core.http import fetch

logger = logutil.init_logger(__name__)


Settings = dict[str, Any]


def api_key(settings: Settings) -> str:
    """The server's YouTube Data API key."""
    return str(settings.get("youtubeApiKey") or "").strip()


def api_url(settings: Settings) -> str:
    """The YouTube Data API base URL, without a trailing slash."""
    return str(settings.get("youtubeApiUrl") or "").strip().rstrip("/")


def configured(settings: Settings) -> bool:
    return bool(api_key(settings) and api_url(settings))


async def youtube_api(settings: Settings, endpoint: str, **params: str | int) -> dict[str, Any]:
    """GET a YouTube Data API endpoint with the server's key (in a header)."""
    data = await fetch(
        f"{api_url(settings)}/{endpoint}",
        return_type="json",
        params=params,
        headers={"X-Goog-Api-Key": api_key(settings)},
    )
    logger.debug(data)
    return data


async def uploads_playlist(settings: Settings, handle: str) -> str:
    """The uploads playlist of the channel ``@handle`` (raises when there is none)."""
    data = await youtube_api(settings, "channels", part="contentDetails", forHandle=handle)
    return data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


async def latest_upload(settings: Settings, uploads: str) -> dict[str, Any]:
    """The snippet of the playlist's latest item (its ``title``, ``resourceId``…)."""
    data = await youtube_api(
        settings, "playlistItems", part="snippet", maxResults=1, playlistId=uploads
    )
    return data["items"][0]["snippet"]


__all__ = [
    "api_key",
    "api_url",
    "configured",
    "latest_upload",
    "uploads_playlist",
    "youtube_api",
]
