"""Pure Spotify API helpers (no Discord imports).

Owns the OAuth bootstrap, the Spotify client, the token-status helper used by
the Web UI auth panel, and the MongoDB document formatter.

spotipy is synchronous: every call is a blocking ``requests`` round trip, with
retries and — on a 429 — a ``time.sleep`` for as long as Spotify asks. Called
from a coroutine, that freezes the whole bot: no gateway heartbeat, no
interaction, no task, until the watchdog restarts the process. So coroutines
use :data:`spotify_api` (``await spotify_api.track(...)``), which runs each
call on a dedicated worker thread. :data:`sp`, the underlying synchronous
client, is for code that already runs on that thread.
"""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import requests
import spotipy

from src.core import logging as logutil
from src.core.config import config_store

logger = logutil.init_logger(__name__)

SPOTIFY_SCOPE = (
    "playlist-modify-private playlist-read-private "
    "playlist-modify-public playlist-read-collaborative"
)
SPOTIFY_CACHE_PATH = "data/.cache"


def _spotify_config() -> dict[str, Any]:
    return config_store.get().get("config", {}).get("spotify", {})


def build_oauth(redirect_uri: str | None = None) -> spotipy.SpotifyOAuth:
    """Build a :class:`spotipy.SpotifyOAuth` from the live config.

    Pass *redirect_uri* to override the configured one — useful when the
    Web UI uses its own ``/spotify/auth/callback`` route instead of the
    historical value in ``spotify.spotifyRedirectUri``.
    """
    cfg = _spotify_config()
    return spotipy.SpotifyOAuth(
        client_id=cfg.get("spotifyClientId", ""),
        client_secret=cfg.get("spotifyClientSecret", ""),
        redirect_uri=redirect_uri or cfg.get("spotifyRedirectUri", ""),
        scope=SPOTIFY_SCOPE,
        open_browser=False,
        cache_handler=spotipy.CacheFileHandler(SPOTIFY_CACHE_PATH),
    )


class _LazySpotifyClient:
    """Module-level proxy that builds the real spotipy client on first use.

    Building at import time used to crash the bot when the token cache was
    missing. The proxy defers construction until the first API call so the
    bot starts and the Web UI can host the (re-)authorization flow.
    """

    def __init__(self) -> None:
        self._client: spotipy.Spotify | None = None
        self._lock = threading.Lock()

    def _resolve(self) -> spotipy.Spotify:
        with self._lock:
            if self._client is None:
                oauth = build_oauth()
                if oauth.get_cached_token() is None:
                    logger.warning("Spotify token cache is empty — re-authorize via the dashboard.")
                self._client = spotipy.Spotify(auth_manager=oauth, language="fr")
            return self._client

    def reset(self) -> None:
        """Drop the cached client so the next call rebuilds it (call after re-auth)."""
        with self._lock:
            self._client = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


sp: _LazySpotifyClient = _LazySpotifyClient()

# One worker: Spotify calls stay in order and never race each other on the
# token refresh or its cache file (spotipy isn't thread-safe there), and none
# of them runs on an event loop. Loop-agnostic, so the bot loop and the Web
# UI's uvicorn loop share it.
_SPOTIFY_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spotify")


async def run_spotify[T](func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking spotipy-related call on the Spotify worker thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_SPOTIFY_EXECUTOR, lambda: func(*args, **kwargs))


class _AsyncSpotifyClient:
    """``await spotify_api.<method>(...)`` for every :class:`spotipy.Spotify` method.

    The attribute is looked up on :data:`sp` inside the worker too: the first
    access builds the client, which reads the token cache from disk.
    """

    def __getattr__(self, name: str) -> Callable[..., Any]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            return await run_spotify(lambda: getattr(sp, name)(*args, **kwargs))

        call.__name__ = name
        return call


spotify_api = _AsyncSpotifyClient()

# What a failed Spotify call raises: spotipy wraps HTTP errors, but connection
# errors and timeouts come straight from its transport (requests) — they are
# not the builtin ConnectionError.
SPOTIFY_ERRORS: tuple[type[Exception], ...] = (
    spotipy.SpotifyException,
    requests.exceptions.RequestException,
)


def get_token_status() -> dict[str, Any]:
    """Inspect the cached Spotify token without making any API calls."""
    cfg = _spotify_config()
    if not cfg.get("spotifyClientId") or not cfg.get("spotifyClientSecret"):
        return {"configured": False, "authorized": False}
    oauth = build_oauth()
    token = oauth.get_cached_token()
    if not token:
        return {"configured": True, "authorized": False}
    return {
        "configured": True,
        "authorized": True,
        "expires_at": token.get("expires_at"),
        "scope": token.get("scope"),
        "is_expired": oauth.is_token_expired(token),
    }


async def fetch_account_summary() -> dict[str, Any] | None:
    """Return ``{id, display_name}`` for the authorized user, or ``None``."""
    try:
        me = await spotify_api.current_user()
    except Exception as e:
        logger.warning("Failed to fetch Spotify account: %s", e)
        return None
    if not me:
        return None
    return {"id": me.get("id"), "display_name": me.get("display_name")}


def spotifymongoformat(track, user=None, spotify2discord=None):
    """Flatten a Spotify track payload into the document shape stored in MongoDB.

    Accepts both playlist-item payloads (``{"track": {...}, "added_at": ...}``)
    and raw track payloads. ``user`` overrides the ``added_by`` attribution;
    otherwise ``spotify2discord`` is used to map the Spotify uploader to a
    Discord user id.
    """
    now_iso = datetime.now(UTC)
    if track.get("track", None):
        inner = track["track"]
        return {
            "_id": str(inner.get("id", None)),
            "added_by": str(
                user
                if user
                else spotify2discord.get(track["added_by"]["id"], track["added_by"]["id"])
            ),
            "added_at": track.get("added_at", now_iso),
            "duration_ms": inner["duration_ms"],
            "name": inner["name"],
            "artists": [artist.get("name") for artist in inner["artists"]],
            "album": inner["album"].get("name"),
        }

    return {
        "_id": str(track.get("id", None)),
        "added_by": str(
            user if user else spotify2discord.get(track["added_by"]["id"], track["added_by"]["id"])
        ),
        "added_at": track.get("added_at", now_iso),
        "duration_ms": track["duration_ms"],
        "name": track["name"],
        "artists": [artist.get("name") for artist in track["artists"]],
        "album": track["album"].get("name"),
    }
