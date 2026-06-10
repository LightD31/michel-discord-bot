"""Infraction browser endpoints for the WebUI moderation view.

Read-only listing plus a revoke action. Revoking flips a case to inactive in
MongoDB and posts a best-effort note to the guild's modlog channel via the bot
loop (mirroring how ``routes/rolemenus.py`` calls back into Discord).
"""

import asyncio
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from features.moderation import Infraction, InfractionType, ModerationRepository
from src.core import logging as logutil
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.moderation")


def _infraction_to_dict(inf: Infraction) -> dict:
    return {
        "id": inf.id,
        "guild_id": inf.guild_id,
        "user_id": inf.user_id,
        "moderator_id": inf.moderator_id,
        "type": inf.type,
        "reason": inf.reason,
        "duration_seconds": inf.duration_seconds,
        "active": inf.active,
        "source": inf.source,
        "created_at": inf.created_at.isoformat() if inf.created_at else None,
        "expires_at": inf.expires_at.isoformat() if inf.expires_at else None,
    }


def _run_on_bot_loop(ctx: WebUIContext, coro):
    bot_loop = ctx.require_bot_loop()
    future = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return asyncio.wrap_future(future)


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/infractions")
    async def api_list_infractions(
        request: Request,
        server_id: str,
        user_id: str | None = Query(default=None),
        type: str | None = Query(default=None),
        active: str | None = Query(default=None),
    ):
        ctx.require_guild_admin(request, server_id)
        active_filter: bool | None = None
        if active in ("true", "1"):
            active_filter = True
        elif active in ("false", "0"):
            active_filter = False
        repo = ModerationRepository(server_id)
        try:
            items = await repo.list(
                type=cast(InfractionType | None, type or None),
                user_id=user_id or None,
                active=active_filter,
                limit=500,
            )
        except Exception as e:
            logger.error("List infractions failed for %s: %s", server_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        return JSONResponse({"infractions": [_infraction_to_dict(i) for i in items]})

    @router.delete("/api/servers/{server_id}/infractions/{case_id}")
    async def api_revoke_infraction(request: Request, server_id: str, case_id: int):
        ctx.require_guild_admin(request, server_id)
        repo = ModerationRepository(server_id)
        try:
            existing = await repo.get(case_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Cas introuvable")
            if not existing.active:
                raise HTTPException(status_code=400, detail="Cas déjà révoqué")
            await repo.set_active(case_id, False)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Revoke infraction failed for %s/%s: %s", server_id, case_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e

        async def _post_note():
            cfg = ctx.get_full_config()
            chan_id = (
                cfg.get("servers", {})
                .get(str(server_id), {})
                .get("moduleModeration", {})
                .get("modLogChannelId")
            )
            if not chan_id:
                return
            try:
                channel = await ctx.bot.fetch_channel(int(chan_id))
            except Exception:
                return
            if channel is None or not hasattr(channel, "send"):
                return
            try:
                await channel.send(f"♻️ Cas #{case_id} révoqué via le dashboard.")
            except Exception as e:
                logger.warning("Could not post revocation note: %s", e)

        try:
            await _run_on_bot_loop(ctx, _post_note())
        except Exception as e:
            logger.warning("Revocation note dispatch failed: %s", e)

        logger.info("Infraction #%s revoked via WebUI for guild %s", case_id, server_id)
        return JSONResponse({"status": "ok"})

    return router
