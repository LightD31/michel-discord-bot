"""The Secret Santa card on the module page: the guild's session, and its draw.

``GET /api/servers/{id}/secretsanta`` describes the guild's session: its
details, the participants (names, and whether they left a wish list) and, once
drawn, how many parcels were marked sent and received. Never who gives to
whom, nor who sent or received: counts alone, as ``/secretsanta participants``
shows them.

``POST …/secretsanta/draw`` runs the same draw as ``/secretsanta tirage``
(:func:`src.discord_ext.secretsanta.run_draw`) on the bot loop, for the same
people: the session's creator or an administrator of the server, read live.
The dashboard bar (``MANAGE_GUILD``) is not enough, as in Discord.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from features.secretsanta import SecretSantaRepository, SecretSantaSession
from features.secretsanta.dates import format_exchange_date
from src.core import logging as logutil
from src.core.config import ModuleConfig, load_discord2name
from src.core.errors import DatabaseError
from src.core.permissions import ADMINISTRATOR, has_any
from src.discord_ext.messages import fetch_user_safe
from src.discord_ext.secretsanta import run_draw
from src.webui import audit
from src.webui.auth import Session
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.secretsanta")

SANTA = ModuleConfig("moduleSecretSanta")
DB_UNAVAILABLE = "Base de données injoignable, réessaie dans un instant."
# A draw DMs every participant: give it time, and never cut it short.
DRAW_TIMEOUT_SECONDS = 120.0


class DrawRequest(BaseModel):
    allow_subgroups: bool = False


async def member_names(bot: Any, guild_id: str, ids: list[int]) -> dict[str, str]:
    """Display names (the server's first names first) of *ids*, on the bot loop."""
    first_names = load_discord2name(guild_id)
    guild = bot.get_guild(int(guild_id))
    names = {}
    for uid in ids:
        member = guild.get_member(uid) if guild is not None else None
        if member is not None:
            name = member.display_name
        else:
            name, _ = await fetch_user_safe(bot, uid)
        names[str(uid)] = str(first_names.get(str(uid)) or name)
    return names


async def summary(
    repo: SecretSantaRepository, session: SecretSantaSession, names: dict[str, str]
) -> dict[str, Any]:
    context_id = session.context_id
    wishlists = await repo.wishlists(context_id)
    total = len(session.participants)
    data: dict[str, Any] = {
        "is_drawn": session.is_drawn,
        "budget": session.budget,
        "exchange_date": session.exchange_date,
        "exchange_label": (
            format_exchange_date(session.exchange_day) if session.exchange_day else None
        ),
        "shipping": session.shipping,
        "created_by": names.get(str(session.created_by), str(session.created_by)),
        "participants": [
            {
                "id": str(uid),
                "name": names.get(str(uid), str(uid)),
                "has_wishlist": uid in wishlists,
            }
            for uid in session.participants
        ],
        "total": total,
    }
    if session.is_drawn and session.shipping:
        sent, received = await repo.gift_marks(context_id)
        data["sent"], data["received"] = len(sent), len(received)
    return data


async def _may_draw(
    ctx: WebUIContext, session: Session, server_id: str, santa: SecretSantaSession
) -> None:
    """403 unless the user could draw in Discord: the creator, or an administrator."""
    if str(santa.created_by) == str(session.user_id):
        return
    access = await ctx.guild_access(session, server_id)
    if not (access.is_owner or has_any(access.permissions, ADMINISTRATOR)):
        raise HTTPException(
            status_code=403,
            detail="Seul le créateur de la session ou un administrateur peut lancer le tirage.",
        )


async def draw_on_bot(
    bot: Any, server_id: str, santa: SecretSantaSession, allow_subgroups: bool
) -> dict[str, Any]:
    """Run the draw (on the bot loop) and say what it did."""
    guild = bot.get_guild(int(server_id))
    settings = SANTA.get(server_id)
    # Shielded: a dashboard that stops waiting must not leave a draw half sent.
    outcome = await asyncio.shield(
        run_draw(
            bot,
            SecretSantaRepository(),
            santa,
            allow_subgroups=allow_subgroups,
            relay=bool(settings.get("anonymousMessages", True)),
            context_name=getattr(guild, "name", None) or server_id,
        )
    )
    if outcome.error:
        raise HTTPException(status_code=409, detail=outcome.error)
    failed = await member_names(bot, server_id, outcome.failed_dms)
    return {
        "count": outcome.count,
        "subgroups": outcome.subgroups,
        "failed_dms": list(failed.values()),
    }


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    async def _session(server_id: str) -> SecretSantaSession | None:
        try:
            return await SecretSantaRepository().get_session(f"guild_{server_id}")
        except DatabaseError as e:
            logger.warning("Secret Santa session read failed for %s: %s", server_id, e)
            raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from e

    @router.get("/api/servers/{server_id}/secretsanta")
    async def api_secretsanta(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        santa = await _session(server_id)
        if santa is None:
            return JSONResponse({"session": None})
        ids = list(dict.fromkeys([santa.created_by, *santa.participants]))
        names = await ctx.call_bot(
            member_names, ctx.bot, server_id, ids, what=f"name Secret Santa members of {server_id}"
        )
        try:
            data = await summary(SecretSantaRepository(), santa, names)
        except DatabaseError as e:
            logger.warning("Secret Santa summary failed for %s: %s", server_id, e)
            raise HTTPException(status_code=503, detail=DB_UNAVAILABLE) from e
        return JSONResponse({"session": data})

    @router.post("/api/servers/{server_id}/secretsanta/draw")
    async def api_secretsanta_draw(request: Request, server_id: str, body: DrawRequest):
        session = await ctx.require_guild_admin(request, server_id)
        ctx.require_bot_loop()
        santa = await _session(server_id)
        if santa is None:
            raise HTTPException(status_code=404, detail="Aucune session de Père Noël Secret.")
        await _may_draw(ctx, session, server_id, santa)
        result = await ctx.call_bot(
            draw_on_bot,
            ctx.bot,
            server_id,
            santa,
            body.allow_subgroups,
            what=f"Secret Santa draw in {server_id}",
            timeout=DRAW_TIMEOUT_SECONDS,
        )
        logger.info(
            "Secret Santa drawn via WebUI for guild %s (by %s/%s)",
            server_id,
            session.username,
            session.user_id,
        )
        await audit.record(
            server_id, session, "secretsanta.draw", details={"participants": result["count"]}
        )
        return JSONResponse(result)

    return router
