"""Spotify, one account per server (P36). No Discord imports.

Each server that runs the Spotify module brings a Spotify app
(``moduleSpotify.spotifyClientId`` / ``spotifyClientSecret``) and connects an
account from its module page (``src/webui/routes/spotify.py``). The account's
token is kept in ``data/spotify/<guild>.json``, readable by the bot only. No
server ever reads or writes another's playlists through someone else's account.

spotipy is synchronous: every call is a blocking ``requests`` round trip, with
retries and — on a 429 — a ``time.sleep`` for as long as Spotify asks. Called
from a coroutine, that freezes the whole bot: no gateway heartbeat, no
interaction, no task, until the watchdog restarts the process. So coroutines
use :func:`spotify_for` (``await spotify_for(guild_id).track(...)``), which
runs each call on a dedicated worker thread. :func:`blocking_client`, the
underlying synchronous client, is for code that already runs on that thread.
"""

import asyncio
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import spotipy

from src.core import logging as logutil
from src.core.backup_format import make_private_dir, open_private
from src.core.config import config_store
from src.core.errors import NotConnected, register_expected_failure

logger = logutil.init_logger(__name__)

SPOTIFY_SCOPE = (
    "playlist-modify-private playlist-read-private "
    "playlist-modify-public playlist-read-collaborative"
)
TOKEN_DIR = Path("data/spotify")
CALLBACK_PATH = "/spotify/auth/callback"


class SpotifyNotConnected(NotConnected):
    """The server has no Spotify app, or no account connected to it."""

    def __init__(self) -> None:
        super().__init__("Spotify")


def token_path(guild_id: object) -> Path:
    """Where *guild_id*'s account token is kept (the id, as digits, names the file)."""
    return TOKEN_DIR / f"{int(str(guild_id))}.json"


def app_credentials(guild_id: object) -> tuple[str, str]:
    """The server's Spotify app, read even while the module is off (to connect first)."""
    server = config_store.get().get("servers", {}).get(str(guild_id)) or {}
    settings = server.get("moduleSpotify") or {}
    return (
        str(settings.get("spotifyClientId") or "").strip(),
        str(settings.get("spotifyClientSecret") or "").strip(),
    )


class _AccountFile(spotipy.CacheHandler):
    """The server's token file, written ``0600``.

    Missing, it raises :class:`SpotifyNotConnected`: spotipy would otherwise
    ask for an authorization code on stdin and hang the Spotify worker.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def stored(self) -> dict[str, Any] | None:
        try:
            token = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            logger.warning("Spotify token %s unreadable: %s", self.path, e)
            return None
        return token if isinstance(token, dict) else None

    def get_cached_token(self) -> dict[str, Any]:
        token = self.stored()
        if token is None:
            raise SpotifyNotConnected()
        return token

    def save_token_to_cache(self, token_info: dict[str, Any]) -> None:
        make_private_dir(self.path.parent)
        with open_private(self.path, "w") as f:
            json.dump(token_info, f)


def callback_uri() -> str:
    """The dashboard's callback, which each server's app lists (``webui.baseUrl`` + path)."""
    base = str(config_store.get().get("config", {}).get("webui", {}).get("baseUrl") or "")
    return base.rstrip("/") + CALLBACK_PATH


def build_oauth(guild_id: object) -> spotipy.SpotifyOAuth:
    """The server's :class:`spotipy.SpotifyOAuth`, from its live settings.

    spotipy wants the redirect URI even to refresh a token, which doesn't send
    it: without a ``webui.baseUrl`` the bare path stands in.
    """
    client_id, client_secret = app_credentials(guild_id)
    return spotipy.SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=callback_uri(),
        scope=SPOTIFY_SCOPE,
        open_browser=False,
        cache_handler=_AccountFile(token_path(guild_id)),
    )


def is_connected(guild_id: object) -> bool:
    """Whether the server has its app and a connected account (one ``stat``, no API call)."""
    return all(app_credentials(guild_id)) and token_path(guild_id).is_file()


# One client per server and app, built on first use. Keyed by the app too, so
# credentials changed on the dashboard take effect at the next call.
_clients: dict[tuple[str, str, str], spotipy.Spotify] = {}
_clients_lock = threading.Lock()


def blocking_client(guild_id: object) -> spotipy.Spotify:
    """The server's synchronous client: only for code on the Spotify worker."""
    guild = str(guild_id)
    client_id, client_secret = app_credentials(guild)
    if not client_id or not client_secret:
        raise SpotifyNotConnected()
    key = (guild, client_id, client_secret)
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            for old in [k for k in _clients if k[0] == guild]:
                del _clients[old]
            client = spotipy.Spotify(auth_manager=build_oauth(guild), language="fr")
            _clients[key] = client
        return client


def reset_client(guild_id: object) -> None:
    """Forget the server's client, so the next call rebuilds it (after a new connection)."""
    with _clients_lock:
        for key in [k for k in _clients if k[0] == str(guild_id)]:
            del _clients[key]


# One worker: Spotify calls stay in order and never race each other on a
# token refresh or its file (spotipy isn't thread-safe there), and none of
# them runs on an event loop. Loop-agnostic, so the bot loop and the Web UI's
# uvicorn loop share it.
_SPOTIFY_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spotify")


async def run_spotify[T](func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking spotipy-related call on the Spotify worker thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_SPOTIFY_EXECUTOR, lambda: func(*args, **kwargs))


class SpotifyClient:
    """``await spotify_for(guild_id).<method>(...)`` for every :class:`spotipy.Spotify` method.

    The client is looked up inside the worker: building it reads the token
    file. A server with no app or no account raises :class:`SpotifyNotConnected`.
    """

    def __init__(self, guild_id: object) -> None:
        self.guild_id = str(guild_id)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            return await run_spotify(
                lambda: getattr(blocking_client(self.guild_id), name)(*args, **kwargs)
            )

        call.__name__ = name
        return call


def spotify_for(guild_id: object) -> SpotifyClient:
    """The server's Spotify account, every call on the worker thread."""
    return SpotifyClient(guild_id)


# What a failed Spotify call raises: spotipy wraps HTTP errors, but connection
# errors and timeouts come straight from its transport (requests) — they are
# not the builtin ConnectionError.
SPOTIFY_ERRORS: tuple[type[Exception], ...] = (
    spotipy.SpotifyException,
    requests.exceptions.RequestException,
)


def is_spotify_outage(error: BaseException) -> bool:
    """A transport failure, a rate limit or a 5xx from Spotify: not a bug in the bot."""
    if isinstance(error, requests.exceptions.RequestException):
        return True
    status = getattr(error, "http_status", None) or 0
    return isinstance(error, spotipy.SpotifyException) and (status == 429 or status >= 500)


register_expected_failure(is_spotify_outage)


def get_token_status(guild_id: object) -> dict[str, Any]:
    """The server's connection as its token file says, without any API call."""
    if not all(app_credentials(guild_id)):
        return {"configured": False, "authorized": False}
    oauth = build_oauth(guild_id)
    token = _AccountFile(token_path(guild_id)).stored()
    if not token:
        return {"configured": True, "authorized": False}
    return {
        "configured": True,
        "authorized": True,
        "expires_at": token.get("expires_at"),
        "scope": token.get("scope"),
        "is_expired": oauth.is_token_expired(token),
    }


async def fetch_account_summary(guild_id: object) -> dict[str, Any] | None:
    """``{id, display_name}`` of the server's connected account, or ``None``."""
    try:
        me = await spotify_for(guild_id).current_user()
    except Exception as e:
        logger.warning("Failed to fetch the Spotify account of %s: %s", guild_id, e)
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
