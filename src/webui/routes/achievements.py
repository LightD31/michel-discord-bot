"""The Succès module page's card: each badge, whether it is in play and how many won it (P25).

``GET /api/servers/{id}/achievements``, for the guild's admins: every badge,
in play or not (switched off here, or its module off, named), and the number
of members holding it. Counts only, never who.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from features.achievements import BADGES, AchievementsRepository
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.errors import DatabaseError
from src.discord_ext.look import look_of
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.achievements")

ACHIEVEMENTS = ModuleConfig("moduleAchievements")


def why_off(guild_id: str, settings: dict[str, Any], badge: Any) -> str | None:
    """``None`` when *badge* is in play, else why not, in French."""
    if settings.get(badge.setting, True) is False:
        return "désactivé ici"
    if not ModuleConfig(badge.module).enabled(guild_id):
        return f"module {look_of(badge.module).label} désactivé"
    return None


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/achievements")
    async def api_achievements(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        if not ACHIEVEMENTS.enabled(server_id):
            raise HTTPException(status_code=404, detail="Le module Succès n'est pas activé ici.")
        settings = ACHIEVEMENTS.get(server_id)
        try:
            holders = await AchievementsRepository(server_id).holders()
        except DatabaseError as e:
            logger.warning("Badge holders of %s unreadable: %s", server_id, e)
            raise HTTPException(
                status_code=503, detail="Base de données injoignable, réessaie dans un instant."
            ) from e
        return JSONResponse(
            {
                "badges": [
                    {
                        "key": b.key,
                        "emoji": b.emoji,
                        "name": b.name,
                        "goal": b.goal,
                        "off": why_off(server_id, settings, b),
                        "holders": holders.get(b.key, 0),
                    }
                    for b in BADGES
                ]
            }
        )

    return router


__all__ = ["create_router", "why_off"]
