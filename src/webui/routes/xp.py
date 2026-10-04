"""XP statistics endpoint for the WebUI XP module page.

Read-only: aggregates the guild's ``xp`` totals and ``xp_events`` log into
chart-ready series (daily activity split by message/voice, level
distribution, top members, weekday × hour heatmap). Shaping lives in
``features.xp.stats`` so it stays unit-testable without MongoDB.
"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from features.userinfo.repository import UserInfoRepository
from features.xp import (
    ALLOWED_RANGES,
    STATS_TIMEZONE,
    TOP_MEMBERS_LIMIT,
    VOICE_TICK_SECONDS,
    XpRepository,
    build_heatmap,
    fill_daily_series,
    level_distribution,
    stats_window,
    summarize,
    top_members,
)
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.xp")


def validate_range(days: int) -> None:
    """Reject a stats window the dashboard doesn't offer (400)."""
    if days not in ALLOWED_RANGES:
        accepted = ", ".join(map(str, ALLOWED_RANGES))
        raise HTTPException(
            status_code=400, detail=f"Période invalide (valeurs acceptées : {accepted})"
        )


async def collect_xp_stats(server_id: str, days: int) -> dict:
    """Chart-ready XP statistics for *server_id* over the last *days* days.

    Shared by the admin module page and the public dashboard
    (``routes/public.py``). Raises ``DatabaseError`` when MongoDB fails.
    """
    first_day, last_day, since = stats_window(days, STATS_TIMEZONE, datetime.now(UTC))
    repo = XpRepository(server_id)
    docs, activity, active_by_day, active_total, heatmap = await asyncio.gather(
        repo.list_all_sorted_by_xp(),
        repo.daily_activity(since, STATS_TIMEZONE),
        repo.daily_active_users(since, STATS_TIMEZONE),
        repo.count_active_users(since),
        repo.activity_heatmap(since, STATS_TIMEZONE),
    )
    top_ids = [str(d.get("_id", "")) for d in docs[:TOP_MEMBERS_LIMIT]]
    names = await UserInfoRepository(server_id).get_names(top_ids)

    daily = fill_daily_series(activity, active_by_day, first_day, last_day)
    voice_ticks = sum(d["voice_ticks"] for d in daily)
    summary = {
        **summarize(docs),
        "period_xp": sum(d["message_xp"] + d["voice_xp"] for d in daily),
        "period_messages": sum(d["messages"] for d in daily),
        "period_voice_ticks": voice_ticks,
        # Each tick stands for one VOICE_TICK_SECONDS slice of presence.
        "period_voice_minutes": voice_ticks * VOICE_TICK_SECONDS // 60,
        "active_members": active_total,
    }
    return {
        "days": days,
        "timezone": STATS_TIMEZONE,
        "summary": summary,
        "daily": daily,
        "levels": level_distribution(docs),
        "top": top_members(docs, names),
        "heatmap": build_heatmap(heatmap),
    }


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/xp/stats")
    async def api_xp_stats(request: Request, server_id: str, days: int = Query(default=30)):
        ctx.require_guild_admin(request, server_id)
        validate_range(days)
        try:
            stats = await collect_xp_stats(server_id, days)
        except DatabaseError as e:
            logger.error("XP stats failed for %s: %s", server_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        return JSONResponse(stats)

    return router
