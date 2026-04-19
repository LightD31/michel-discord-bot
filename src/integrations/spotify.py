"""Pure Spotify API helpers (no Discord imports).

Contains the OAuth bootstrap and MongoDB document formatter. Discord embed
builders and anything that depends on ``interactions`` live in
``extensions/spotify/_common.py``.
"""

import os
from datetime import UTC, datetime

import spotipy

from src.core import logging as logutil
from src.core.config import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, _, _ = load_config()


def spotify_auth() -> spotipy.Spotify:
    """Authenticate with Spotify and return a cached ``spotipy.Spotify`` client."""
    sp_oauth = spotipy.SpotifyOAuth(
        client_id=config["spotify"]["spotifyClientId"],
        redirect_uri=config["spotify"]["spotifyRedirectUri"],
        client_secret=config["spotify"]["spotifyClientSecret"],
        scope="playlist-modify-private playlist-read-private",
        open_browser=False,
        cache_handler=spotipy.CacheFileHandler("data/.cache"),
    )

    token_info = sp_oauth.get_cached_token()

    if (
        not token_info
        or sp_oauth.is_token_expired(token_info)
        or not sp_oauth.validate_token(token_info)
    ):
        if token_info:
            logger.warning("Cached token has expired or is invalid.")
        auth_url = sp_oauth.get_authorize_url()
        logger.warning("Please visit this URL to authorize the application: %s", auth_url)

    return spotipy.Spotify(auth_manager=sp_oauth, language="fr")


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
