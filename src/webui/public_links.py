"""Which per-guild public dashboards exist, and where they live.

Framework-free on purpose: ``routes/public.py`` uses it to gate the
unauthenticated endpoints, and extensions use it to link to the pages from
Discord embeds without importing FastAPI.

A public dashboard is opt-in per guild: the module must be enabled *and* its
``publicDashboard`` flag set from the module's Web UI page. The pages are
served by the SPA at ``/public/{token}/{kind}``, where ``token`` is a random
per-guild secret (``servers.<id>.publicDashboardToken``). The guild id alone
opens nothing, since ids are visible to every member. The token is minted on
first use and an admin can rotate it to revoke a leaked link.
"""

import secrets
from collections.abc import Callable, Mapping
from typing import Any

from src.core.config import config_store

# Dashboard kind (URL segment) -> module config key that owns it.
PUBLIC_DASHBOARDS: dict[str, str] = {
    "xp": "moduleXp",
    "spotify": "moduleSpotify",
}

PUBLIC_FLAG = "publicDashboard"
# Guild-level key (a plain string next to the module dicts, so the config
# cleanup, which only strips keys inside module dicts, leaves it alone).
TOKEN_KEY = "publicDashboardToken"
# 16 random bytes -> 22 URL-safe characters (128 bits).
TOKEN_BYTES = 16
MIN_TOKEN_LENGTH = 16


def public_dashboard_enabled(server_config: dict[str, Any], kind: str) -> bool:
    """True if *kind*'s public page is switched on in this guild's config."""
    module_key = PUBLIC_DASHBOARDS.get(kind)
    if module_key is None:
        return False
    module = server_config.get(module_key) or {}
    return bool(module.get("enabled")) and bool(module.get(PUBLIC_FLAG))


def enabled_public_dashboards(server_config: dict[str, Any]) -> list[str]:
    """The public dashboard kinds this guild exposes, in display order."""
    return [kind for kind in PUBLIC_DASHBOARDS if public_dashboard_enabled(server_config, kind)]


def public_token(server_config: Mapping[str, Any]) -> str:
    """The guild's current public token, or ``""`` if none was minted yet."""
    token = server_config.get(TOKEN_KEY)
    return token if isinstance(token, str) else ""


def find_guild_by_token(
    servers: Mapping[str, Any], token: str
) -> tuple[str, dict[str, Any]] | None:
    """Resolve a URL token to ``(guild_id, server_config)``, or ``None``.

    Compares in constant time; short or non-ASCII candidates never match.
    """
    if len(token) < MIN_TOKEN_LENGTH or not token.isascii():
        return None
    candidate = token.encode()
    match = None
    for guild_id, server in servers.items():
        if not isinstance(server, dict):
            continue
        stored = public_token(server)
        if stored and secrets.compare_digest(stored.encode(), candidate):
            match = (str(guild_id), server)
    return match


def ensure_public_token(
    guild_id: str | int,
    *,
    rotate: bool = False,
    mutate: Callable[[Callable[[dict[str, Any]], None]], Any] | None = None,
) -> str:
    """Return the guild's public token, minting (or with *rotate*, replacing) it.

    *mutate* is the atomic read-modify-write to persist with; it defaults to
    ``config_store.mutate`` (routes pass ``ctx.mutate_config`` for HTTP-shaped
    errors). Writes only when the token actually changes.
    """
    gid = str(guild_id)
    if not rotate:
        existing = public_token(config_store.get().get("servers", {}).get(gid) or {})
        if existing:
            return existing

    minted: dict[str, str] = {}

    def mutator(data: dict[str, Any]) -> None:
        server = data.setdefault("servers", {}).setdefault(gid, {})
        current = public_token(server)
        if rotate or not current:
            current = secrets.token_urlsafe(TOKEN_BYTES)
            server[TOKEN_KEY] = current
        minted["token"] = current

    (mutate or config_store.mutate)(mutator)
    return minted["token"]


def public_dashboard_url(global_config: dict[str, Any], token: str, kind: str) -> str:
    """Absolute URL of a public *kind* page, or ``""`` when unreachable.

    Empty when there is no token, the Web UI is off or ``webui.baseUrl`` is
    unset; callers treat that as "no link", like any other unset URL. Does
    not check the per-guild opt-in; pair it with :func:`public_dashboard_enabled`.
    """
    webui = global_config.get("webui") or {}
    base = str(webui.get("baseUrl") or "").strip().rstrip("/")
    if not token or not webui.get("enabled") or not base:
        return ""
    return f"{base}/public/{token}/{kind}"
