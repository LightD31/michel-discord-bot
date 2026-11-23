"""The bot's Twitch account, connected from the dashboard (P29). No Discord imports.

EventSub and Streamlabs' live status need a *user* token. twitchAPI's
``UserAuthenticationStorageHelper`` keeps it in :data:`TOKEN_PATH` and refreshes
it; when the file is missing or its refresh token is dead, the library falls
back to logging in through a browser on the bot's own machine, which a
container doesn't have. So the extensions bind with :func:`refuse_browser_login`
instead, and a developer connects the account in the dashboard's global
settings: an OAuth authorization-code flow whose callback writes the file in
the format the helper reads (:func:`save_tokens`).

Twitch's endpoints come from twitchAPI (``TWITCH_AUTH_BASE_URL``). The client
secret only travels in a POST body, never in a URL.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from twitchAPI.helper import TWITCH_AUTH_BASE_URL
from twitchAPI.type import AuthScope

from src.core import logging as logutil
from src.core.backup_format import make_private_dir, open_private
from src.core.config import global_config
from src.core.errors import IntegrationError
from src.core.http import http_client

logger = logutil.init_logger(__name__)

TOKEN_PATH = Path("data/twitchcreds.json")
SCOPES = [AuthScope.USER_READ_SUBSCRIPTIONS]


class TwitchNotConnected(IntegrationError):
    """No usable Twitch user token: a developer must connect the account."""


class TwitchAuthError(IntegrationError):
    """Twitch refused the code exchange, or answered something unexpected."""


def app_credentials() -> tuple[str, str]:
    """The bot's Twitch application (global section ``twitch``), ``("", "")`` when unset."""
    section = global_config().get("twitch") or {}
    return str(section.get("twitchClientId") or ""), str(section.get("twitchClientSecret") or "")


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Where to send the developer: Twitch's consent page, back to *redirect_uri*."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scope.value for scope in SCOPES),
        "state": state,
        "force_verify": "true",  # show which account is being connected
    }
    return f"{TWITCH_AUTH_BASE_URL}authorize?{urlencode(params)}"


async def exchange_code(
    client_id: str, client_secret: str, code: str, redirect_uri: str
) -> tuple[str, str]:
    """Trade the authorization *code* for an access and a refresh token."""
    session = await http_client.session()
    form = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    async with session.post(f"{TWITCH_AUTH_BASE_URL}token", data=form) as response:
        data = await response.json(content_type=None)
        if response.status != 200 or not isinstance(data, dict):
            raise TwitchAuthError(f"échange du code refusé (HTTP {response.status})")
    token, refresh = data.get("access_token"), data.get("refresh_token")
    if not isinstance(token, str) or not isinstance(refresh, str):
        raise TwitchAuthError("réponse de Twitch sans jeton")
    return token, refresh


def save_tokens(token: str, refresh: str, path: Path | None = None) -> None:
    """Write the tokens where ``UserAuthenticationStorageHelper`` reads them, ``0600``."""
    path = path or TOKEN_PATH
    make_private_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    with open_private(tmp, "w") as f:
        json.dump({"token": token, "refresh": refresh}, f)
    os.replace(tmp, path)


def stored_tokens(path: Path | None = None) -> dict[str, str] | None:
    """The stored ``{"token", "refresh"}``, or ``None`` when there is no usable file."""
    try:
        data = json.loads((path or TOKEN_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("token") or not data.get("refresh"):
        return None
    return {"token": str(data["token"]), "refresh": str(data["refresh"])}


async def account_status(path: Path | None = None) -> dict[str, Any]:
    """What the dashboard shows: connected or not, as whom, with which scopes.

    The stored token is checked with Twitch (``/validate``). An expired one
    with a refresh token still counts as connected: the extensions refresh it
    when they start.
    """
    tokens = stored_tokens(path)
    if tokens is None:
        return {"connected": False}
    session = await http_client.session()
    headers = {"Authorization": f"OAuth {tokens['token']}"}
    async with session.get(f"{TWITCH_AUTH_BASE_URL}validate", headers=headers) as response:
        data = await response.json(content_type=None)
        status = response.status
    if status == 200 and isinstance(data, dict):
        return {
            "connected": True,
            "login": data.get("login"),
            "scopes": data.get("scopes") or [],
            "expires_in": data.get("expires_in"),
        }
    # 401: expired or revoked. The refresh token tells which, at the next start.
    return {"connected": True, "login": None, "scopes": [], "expires_in": 0, "stale": True}


async def refuse_browser_login(_twitch: Any, _scopes: list[AuthScope]) -> tuple[str, str]:
    """``auth_generator_func`` for ``UserAuthenticationStorageHelper``: never open a browser."""
    raise TwitchNotConnected(
        "Twitch n'est pas connecté : un développeur peut le connecter depuis les "
        "réglages globaux du tableau de bord (section Twitch)."
    )


__all__ = [
    "SCOPES",
    "TOKEN_PATH",
    "TwitchAuthError",
    "TwitchNotConnected",
    "account_status",
    "app_credentials",
    "authorize_url",
    "exchange_code",
    "refuse_browser_login",
    "save_tokens",
    "stored_tokens",
]
