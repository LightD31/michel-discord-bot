"""Unauthenticated, read-only endpoints behind the per-guild public dashboards.

Each guild can publish its XP and Spotify statistics at
``/public/{guild_id}/xp`` and ``/public/{guild_id}/spotify`` (pages served by
the SPA). Opt-in per module through the ``publicDashboard`` flag — see
:mod:`src.webui.public_links`. Anything not explicitly published answers the
same 404 as an unknown guild, so the endpoints don't reveal which guilds the
bot is configured for.

The payloads reuse the admin module-page aggregations with two differences:

- no Discord ids: XP rows keep only the display name, and Spotify user ids are
  replaced by the member's name server-side;
- results are cached per guild (and window) for :data:`CACHE_TTL_SECONDS`, and
  concurrent misses share one computation, so anonymous traffic costs at most
  one MongoDB aggregation per guild per period.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from features.userinfo.repository import UserInfoRepository
from features.xp.cache import TTLCache
from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import DatabaseError
from src.webui.context import WebUIContext
from src.webui.public_links import enabled_public_dashboards, public_dashboard_enabled
from src.webui.routes import spotify as spotify_routes
from src.webui.routes import xp as xp_routes

logger = logutil.init_logger("webui.routes.public")

CACHE_TTL_SECONDS = 300
UNKNOWN_MEMBER = "Membre inconnu"
NOT_FOUND = "Dashboard public introuvable"


def _server_config(server_id: str) -> dict[str, Any]:
    servers = config_store.get().get("servers", {})
    server = servers.get(server_id) if server_id.isdigit() else None
    return server if isinstance(server, dict) else {}


def _require_public(server_id: str, kind: str) -> dict[str, Any]:
    """Return the guild's config, or 404 unless *kind* is published there."""
    server = _server_config(server_id)
    if not public_dashboard_enabled(server, kind):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return server


def _public_xp_payload(stats: dict[str, Any]) -> dict[str, Any]:
    """Drop the Discord id and handle from the top-member rows.

    ``top_members`` falls back to the id when a member has no stored name;
    that fallback becomes :data:`UNKNOWN_MEMBER` here.
    """
    top = []
    for row in stats.get("top", []):
        public_row = {k: v for k, v in row.items() if k not in ("user_id", "username")}
        if not row.get("display_name") or row.get("display_name") == row.get("user_id"):
            public_row["display_name"] = UNKNOWN_MEMBER
        top.append(public_row)
    return {**stats, "top": top}


async def _spotify_label_resolver(
    server_id: str, server: dict[str, Any], user_ids: set[str]
) -> Callable[[str], str]:
    """Same precedence as the admin page: configured first name, then Discord name."""
    prenoms = server.get("discord2name") or {}
    if not isinstance(prenoms, dict):
        prenoms = {}
    names = await UserInfoRepository(server_id).get_names(sorted(user_ids))

    def label_of(user_id: str) -> str:
        info = names.get(user_id, {})
        return str(
            prenoms.get(user_id)
            or info.get("display_name")
            or info.get("username")
            or UNKNOWN_MEMBER
        )

    return label_of


class _CoalescingCache:
    """TTL cache whose concurrent misses on one key share a single computation."""

    def __init__(self, ttl: int) -> None:
        self._cache = TTLCache(ttl=ttl)
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_compute(self, key: str, compute: Callable[[], Awaitable[Any]]) -> Any:
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached is None:
                cached = await compute()
                self._cache.set(key, cached)
        return cached


def _guild_meta(ctx: WebUIContext, server_id: str, server: dict[str, Any]) -> dict[str, Any]:
    """Name and icon hash from the bot's guild cache, falling back to config."""
    guild = None
    if ctx.bot is not None:
        guild = next((g for g in ctx.bot.guilds or [] if str(g.id) == server_id), None)
    name = getattr(guild, "name", None) or server.get("serverName") or f"Serveur {server_id}"
    icon = getattr(getattr(guild, "icon", None), "hash", None)
    return {"id": server_id, "name": name, "icon": icon}


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()
    cache = _CoalescingCache(CACHE_TTL_SECONDS)

    @router.get("/api/public/{server_id}")
    async def api_public_guild(server_id: str):
        """Header data for a guild's public pages, and which pages it publishes."""
        server = _server_config(server_id)
        dashboards = enabled_public_dashboards(server)
        if not dashboards:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return JSONResponse({**_guild_meta(ctx, server_id, server), "dashboards": dashboards})

    @router.get("/api/public/{server_id}/xp/stats")
    async def api_public_xp_stats(server_id: str, days: int = Query(default=30)):
        _require_public(server_id, "xp")
        xp_routes.validate_range(days)

        async def compute() -> dict[str, Any]:
            return _public_xp_payload(await xp_routes.collect_xp_stats(server_id, days))

        try:
            stats = await cache.get_or_compute(f"xp:{server_id}:{days}", compute)
        except DatabaseError as e:
            logger.error("Public XP stats failed for %s: %s", server_id, e)
            raise HTTPException(
                status_code=503, detail="Statistiques momentanément indisponibles."
            ) from e
        return JSONResponse(stats)

    @router.get("/api/public/{server_id}/spotify/stats")
    async def api_public_spotify_stats(server_id: str):
        server = _require_public(server_id, "spotify")

        async def compute() -> dict[str, Any]:
            stats = await spotify_routes.collect_spotify_stats(server_id)
            label_of = await _spotify_label_resolver(server_id, server, stats.user_ids())
            return stats.with_user_labels(label_of).to_dict()

        try:
            stats = await cache.get_or_compute(f"spotify:{server_id}", compute)
        except DatabaseError as e:
            logger.error("Public Spotify stats failed for %s: %s", server_id, e)
            raise HTTPException(
                status_code=503, detail="Statistiques momentanément indisponibles."
            ) from e
        return JSONResponse(stats)

    return router
