"""Read-only endpoints behind the per-guild public dashboards, plus their admin side.

Each guild can publish its XP and Spotify statistics at
``/public/{token}/xp`` and ``/public/{token}/spotify`` (pages served by the
SPA). ``token`` is the guild's random ``publicDashboardToken``, never its id.
Opt-in per module through the ``publicDashboard`` flag; see
:mod:`src.webui.public_links`. An unknown token and an unpublished page answer
the same 404, so the endpoints reveal nothing about which guilds exist.

The payloads reuse the admin module-page aggregations with these differences:

- no Discord ids: XP rows keep only the display name, and Spotify user ids are
  replaced by the member's name server-side;
- the open Spotify poll shows only its title, date, who added the track and
  how many people voted. The tally and the voters' names stay hidden until it
  closes, so the page can't sway it;
- results are cached per guild (and window) for :data:`CACHE_TTL_SECONDS`, and
  concurrent misses share one computation, so anonymous traffic costs at most
  one MongoDB aggregation per guild per period.

Guild admins fetch the link from ``GET /api/servers/{id}/public-dashboard``
(minting the token on first use) and revoke it with
``POST /api/servers/{id}/public-dashboard/rotate``.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from features.userinfo.repository import UserInfoRepository
from features.xp.cache import TTLCache
from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import DatabaseError
from src.webui.context import WebUIContext
from src.webui.public_links import (
    enabled_public_dashboards,
    ensure_public_token,
    find_guild_by_token,
    public_dashboard_enabled,
    public_token,
)
from src.webui.routes import spotify as spotify_routes
from src.webui.routes import xp as xp_routes

logger = logutil.init_logger("webui.routes.public")

CACHE_TTL_SECONDS = 300
UNKNOWN_MEMBER = "Membre inconnu"
NOT_FOUND = "Dashboard public introuvable"


def _resolve(token: str) -> tuple[str, dict[str, Any]]:
    """``(guild_id, server_config)`` for a URL token, or 404."""
    found = find_guild_by_token(config_store.get().get("servers", {}), token)
    if found is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return found


def _require_public(token: str, kind: str) -> tuple[str, dict[str, Any]]:
    """Resolve *token*, or 404 unless *kind* is published for that guild."""
    server_id, server = _resolve(token)
    if not public_dashboard_enabled(server, kind):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return server_id, server


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


def _public_spotify_payload(stats: dict[str, Any]) -> dict[str, Any]:
    """Keep the open poll's identity and turnout; who voted what stays private until it closes."""
    poll = stats.get("current_poll")
    if not poll:
        return stats
    hidden = {
        "name": poll.get("name"),
        "date": poll.get("date"),
        "added_by": poll.get("added_by"),
        "voter_count": len(poll.get("voters") or []),
        "results_hidden": True,
    }
    return {**stats, "current_poll": hidden}


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

    # --- Admin: the guild's public link ----------------------------------

    def _link_payload(server_id: str, token: str) -> JSONResponse:
        server = config_store.get().get("servers", {}).get(server_id) or {}
        return JSONResponse({"token": token, "dashboards": enabled_public_dashboards(server)})

    @router.get("/api/servers/{server_id}/public-dashboard")
    async def api_public_link(request: Request, server_id: str):
        """The guild's public token, minted on first request."""
        session = ctx.require_guild_admin(request, server_id)
        had_token = bool(public_token(config_store.get().get("servers", {}).get(server_id) or {}))
        token = ensure_public_token(server_id, mutate=ctx.mutate_config)
        if not had_token:
            logger.info(
                "Public dashboard token created for guild %s (by %s/%s)",
                server_id,
                session.username,
                session.user_id,
            )
        return _link_payload(server_id, token)

    @router.post("/api/servers/{server_id}/public-dashboard/rotate")
    async def api_rotate_public_link(request: Request, server_id: str):
        """Replace the guild's public token: every previously shared link stops working."""
        session = ctx.require_guild_admin(request, server_id)
        token = ensure_public_token(server_id, rotate=True, mutate=ctx.mutate_config)
        logger.info(
            "Public dashboard token rotated for guild %s (by %s/%s)",
            server_id,
            session.username,
            session.user_id,
        )
        return _link_payload(server_id, token)

    # --- Public pages ------------------------------------------------------

    @router.get("/api/public/{token}")
    async def api_public_guild(token: str):
        """Header data for a guild's public pages, and which pages it publishes."""
        server_id, server = _resolve(token)
        dashboards = enabled_public_dashboards(server)
        if not dashboards:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return JSONResponse({**_guild_meta(ctx, server_id, server), "dashboards": dashboards})

    @router.get("/api/public/{token}/xp/stats")
    async def api_public_xp_stats(token: str, days: int = Query(default=30)):
        server_id, _ = _require_public(token, "xp")
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

    @router.get("/api/public/{token}/spotify/stats")
    async def api_public_spotify_stats(token: str):
        server_id, server = _require_public(token, "spotify")

        async def compute() -> dict[str, Any]:
            stats = await spotify_routes.collect_spotify_stats(server_id)
            label_of = await _spotify_label_resolver(server_id, server, stats.user_ids())
            return _public_spotify_payload(stats.with_user_labels(label_of).to_dict())

        try:
            stats = await cache.get_or_compute(f"spotify:{server_id}", compute)
        except DatabaseError as e:
            logger.error("Public Spotify stats failed for %s: %s", server_id, e)
            raise HTTPException(
                status_code=503, detail="Statistiques momentanément indisponibles."
            ) from e
        return JSONResponse(stats)

    return router
