"""The running giveaways on their module page: the list, "Tirer maintenant" and "Annuler" (P42).

``GET /api/servers/{id}/giveaways`` lists the open giveaways, as ``/concours
liste`` does: the lot, where it was posted, who hosts it, how many entered
and when it ends (counts, never who entered). ``POST …/giveaways/{message
id}/draw`` and ``…/cancel`` do what ``/concours tirer`` and ``/concours
annuler`` do, through the same functions (:mod:`src.discord_ext.giveaway`),
on the bot loop. Those commands are ``MANAGE_GUILD`` by default, the
dashboard's admin bar, and exist only where the module is on: so do the
buttons.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from features.giveaway import Giveaway, GiveawayRepository
from src.core import logging as logutil
from src.discord_ext.giveaway import GIVEAWAY, cancel, draw
from src.discord_ext.messages import jump_url
from src.webui import audit
from src.webui.context import WebUIContext
from src.webui.routes.secretsanta import member_names

logger = logutil.init_logger("webui.routes.giveaways")


def _name_of(guild: Any, getter: str, item_id: str | None) -> str | None:
    """The cached channel's or role's name, or None when it is gone."""
    if not item_id or guild is None:
        return None
    item = getattr(guild, getter)(int(item_id))
    return getattr(item, "name", None) if item is not None else None


async def running_on_bot(bot: Any, server_id: str) -> list[dict[str, Any]]:
    """The server's open giveaways, soonest end first, with names (on the bot loop)."""
    active = await GiveawayRepository(server_id).list_active()
    guild = bot.get_guild(int(server_id))
    hosts = await member_names(bot, server_id, sorted({int(g.host_id) for g in active}))
    return [
        {
            "id": g.message_id,
            "prize": g.prize,
            "ends_at": g.ends_at.isoformat(),
            "winners": g.winners_count,
            "entrants": len(g.entrants),
            "host": hosts.get(g.host_id, g.host_id),
            "channel": _name_of(guild, "get_channel", g.channel_id),
            "role": _name_of(guild, "get_role", g.required_role_id),
            "min_level": g.min_level,
            "url": jump_url(server_id, g.channel_id, g.message_id),
        }
        for g in active
    ]


async def _open_giveaway(server_id: str, message_id: str) -> Giveaway:
    """The open giveaway of *message_id*, or the refusal ``/concours`` would give."""
    giveaway = await GiveawayRepository(server_id).get_by_message(message_id)
    if giveaway is None or giveaway.id is None:
        raise HTTPException(
            status_code=404, detail="Ce giveaway est introuvable : actualise la liste."
        )
    if giveaway.drawn or giveaway.cancelled:
        raise HTTPException(
            status_code=409, detail="Ce giveaway est déjà clos : actualise la liste."
        )
    return giveaway


async def draw_on_bot(bot: Any, server_id: str, message_id: str) -> dict[str, Any]:
    """Draw now, as ``/concours tirer``; the winners' names."""
    giveaway = await _open_giveaway(server_id, message_id)
    repo = GiveawayRepository(server_id)
    outcome = await draw(bot, repo, giveaway)
    if outcome == "taken":
        raise HTTPException(status_code=409, detail="Ce giveaway vient d'être clos.")
    if outcome == "retry":
        raise HTTPException(
            status_code=502, detail="Discord n'a pas répondu, réessaie dans un instant."
        )
    drawn = await repo.get_by_message(message_id)
    winners = drawn.winners if drawn is not None else []
    names = await member_names(bot, server_id, [int(w) for w in winners])
    return {"prize": giveaway.prize, "winners": [names.get(w, w) for w in winners]}


async def cancel_on_bot(bot: Any, server_id: str, message_id: str) -> dict[str, Any]:
    """Cancel without a draw, as ``/concours annuler``."""
    giveaway = await _open_giveaway(server_id, message_id)
    if not await cancel(bot, GiveawayRepository(server_id), giveaway):
        raise HTTPException(status_code=409, detail="Ce giveaway vient d'être clos.")
    return {"prize": giveaway.prize}


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    def _require_on(server_id: str) -> None:
        if not GIVEAWAY.enabled(server_id):
            raise HTTPException(
                status_code=409,
                detail="Les giveaways sont désactivés sur ce serveur : active le module d'abord.",
            )

    def _check_id(message_id: str) -> None:
        if not message_id.isdigit() or len(message_id) > 20:
            raise HTTPException(status_code=404, detail="Ce giveaway est introuvable.")

    @router.get("/api/servers/{server_id}/giveaways")
    async def api_giveaways(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        giveaways = await ctx.call_bot(
            running_on_bot, ctx.bot, server_id, what=f"list the giveaways of {server_id}"
        )
        return JSONResponse({"enabled": GIVEAWAY.enabled(server_id), "giveaways": giveaways})

    @router.post("/api/servers/{server_id}/giveaways/{message_id}/draw")
    async def api_giveaway_draw(request: Request, server_id: str, message_id: str):
        session = await ctx.require_guild_admin(request, server_id)
        _check_id(message_id)
        _require_on(server_id)
        result = await ctx.call_bot(
            draw_on_bot, ctx.bot, server_id, message_id, what=f"draw a giveaway of {server_id}"
        )
        logger.info("Giveaway %s of %s drawn by %s", message_id, server_id, session.username)
        await audit.record(server_id, session, "giveaway.draw", target=result["prize"])
        return JSONResponse(result)

    @router.post("/api/servers/{server_id}/giveaways/{message_id}/cancel")
    async def api_giveaway_cancel(request: Request, server_id: str, message_id: str):
        session = await ctx.require_guild_admin(request, server_id)
        _check_id(message_id)
        _require_on(server_id)
        result = await ctx.call_bot(
            cancel_on_bot, ctx.bot, server_id, message_id, what=f"cancel a giveaway of {server_id}"
        )
        logger.info("Giveaway %s of %s cancelled by %s", message_id, server_id, session.username)
        await audit.record(server_id, session, "giveaway.cancel", target=result["prize"])
        return JSONResponse(result)

    return router
