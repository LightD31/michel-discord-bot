"""The Gazette's card on its module page: a preview of this week, and "Publier maintenant".

``GET /api/servers/{id}/gazette`` gathers this week's edition on the bot loop
(:func:`src.discord_ext.gazette.gather_edition`) and returns it as the post
would read: names, never ids. ``POST …/gazette/publish`` posts it now in the
configured channel, outside the weekly schedule (the weekly edition still
comes), for an admin who could post there by hand (``CAN_SEND``, read live).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core import clock
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.errors import DatabaseError
from src.discord_ext.channels import fetch_guild_channel, member_can
from src.discord_ext.gazette import edition_json, gather_edition, post_edition
from src.discord_ext.messages import CAN_SEND
from src.webui import audit
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.gazette")

GAZETTE = ModuleConfig("moduleGazette")
DB_UNAVAILABLE = "Base de données injoignable, réessaie dans un instant."


async def preview_on_bot(bot: Any, server_id: str) -> dict[str, Any]:
    try:
        edition = await gather_edition(bot, server_id, clock.now())
    except DatabaseError as e:
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from e
    return edition_json(edition)


async def publish_on_bot(bot: Any, server_id: str, user_id: str) -> dict[str, Any]:
    settings = GAZETTE.get(server_id)
    if not settings:
        raise HTTPException(status_code=409, detail="La Gazette n'est pas activée sur ce serveur.")
    channel = await fetch_guild_channel(
        bot, server_id, str(settings.get("gazetteChannelId") or ""), logger=logger
    )
    if channel is None or not hasattr(channel, "send"):
        raise HTTPException(
            status_code=409, detail="Le salon de la Gazette est introuvable : choisis-le à nouveau."
        )
    guild = bot.get_guild(int(server_id))
    member = guild.get_member(int(user_id)) if guild is not None else None
    if not member_can(member, channel, CAN_SEND):
        raise HTTPException(
            status_code=403,
            detail=f"Tu ne peux pas écrire dans #{getattr(channel, 'name', channel.id)}.",
        )
    try:
        edition = await gather_edition(bot, server_id, clock.now())
        message = await post_edition(channel, edition)
    except DatabaseError as e:
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from e
    return {"channel": getattr(channel, "name", ""), "message_id": str(message.id)}


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/gazette")
    async def api_gazette(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        data = await ctx.call_bot(
            preview_on_bot, ctx.bot, server_id, what=f"preview the Gazette of {server_id}"
        )
        return JSONResponse(data)

    @router.post("/api/servers/{server_id}/gazette/publish")
    async def api_gazette_publish(request: Request, server_id: str):
        session = await ctx.require_guild_admin(request, server_id)
        result = await ctx.call_bot(
            publish_on_bot,
            ctx.bot,
            server_id,
            str(session.user_id),
            what=f"publish the Gazette of {server_id}",
        )
        logger.info("Gazette of %s published by %s from the dashboard", server_id, session.username)
        await audit.record(server_id, session, "gazette.publish", target=result["channel"])
        return JSONResponse(result)

    return router
