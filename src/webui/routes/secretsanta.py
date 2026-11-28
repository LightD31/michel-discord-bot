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

``POST …/secretsanta/reveal`` (P15) shows who gives to whom, as
``/secretsanta reveler`` does: to the server's owner and its administrators
only, read live from Discord (not the session's creator, and not
``MANAGE_GUILD``), and written to the audit log. A POST: it is a disclosure,
counted by the mutation limit, never prefetched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.secretsanta import SecretSantaRepository, SecretSantaSession
from features.secretsanta.dates import format_exchange_date
from src.core import logging as logutil
from src.core.config import ModuleConfig, load_discord2name
from src.core.permissions import ADMINISTRATOR, has_any
from src.discord_ext.messages import fetch_user_safe
from src.discord_ext.secretsanta import run_draw
from src.webui import audit
from src.webui.auth import Session
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.secretsanta")

SANTA = ModuleConfig("moduleSecretSanta")
# A draw DMs every participant: give it time, and never cut it short.
DRAW_TIMEOUT_SECONDS = 120.0


class DrawRequest(BaseModel):
    allow_subgroups: bool = False


class PairRequest(BaseModel):
    """Two members who mustn't draw each other (P42)."""

    user1: str = Field(pattern=r"^[0-9]{1,20}$")
    user2: str = Field(pattern=r"^[0-9]{1,20}$")


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


async def _may_organise(
    ctx: WebUIContext, session: Session, server_id: str, santa: SecretSantaSession | None
) -> bool:
    """As in Discord: the session's creator, or an administrator (only one, with no session).

    Exclusions steer the draw: a member who could exclude everyone but one
    target would choose whom they draw.
    """
    if santa is not None and str(santa.created_by) == str(session.user_id):
        return True
    return await _is_administrator(ctx, session, server_id)


async def _is_administrator(ctx: WebUIContext, session: Session, server_id: str) -> bool:
    access = await ctx.guild_access(session, server_id)
    return bool(access.is_owner or has_any(access.permissions, ADMINISTRATOR))


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
        return await SecretSantaRepository().get_session(f"guild_{server_id}")

    async def _exclusions(server_id: str) -> list[dict[str, Any]]:
        pairs = await SecretSantaRepository().read_banned_pairs(f"guild_{server_id}")
        ids = list(dict.fromkeys(uid for pair in pairs for uid in pair))
        names = await ctx.call_bot(
            member_names,
            ctx.bot,
            server_id,
            ids,
            what=f"name Secret Santa exclusions of {server_id}",
        )
        return [
            {
                "user1": {"id": str(a), "name": names.get(str(a), str(a))},
                "user2": {"id": str(b), "name": names.get(str(b), str(b))},
            }
            for a, b in pairs
        ]

    @router.get("/api/servers/{server_id}/secretsanta")
    async def api_secretsanta(request: Request, server_id: str):
        session = await ctx.require_guild_admin(request, server_id)
        santa = await _session(server_id)
        # The excluded pairs only to whom may change them (P42), as /secretsanta paires-exclues.
        organiser = await _may_organise(ctx, session, server_id, santa)
        extra = {
            "exclusions": await _exclusions(server_id) if organiser else None,
            "can_exclude": organiser and not (santa is not None and santa.is_drawn),
        }
        if santa is None:
            return JSONResponse({"session": None, **extra})
        ids = list(dict.fromkeys([santa.created_by, *santa.participants]))
        names = await ctx.call_bot(
            member_names, ctx.bot, server_id, ids, what=f"name Secret Santa members of {server_id}"
        )
        data = await summary(SecretSantaRepository(), santa, names)
        data["can_reveal"] = santa.is_drawn and await _is_administrator(ctx, session, server_id)
        return JSONResponse({"session": data, **extra})

    async def _exclusion_guard(request: Request, server_id: str, body: PairRequest) -> Session:
        session = await ctx.require_guild_admin(request, server_id)
        santa = await _session(server_id)
        if not await _may_organise(ctx, session, server_id, santa):
            raise HTTPException(
                status_code=403,
                detail="Seul le créateur de la session ou un administrateur peut gérer les exclusions.",
            )
        if santa is not None and santa.is_drawn:
            raise HTTPException(
                status_code=409,
                detail="Le tirage a déjà eu lieu : crée une nouvelle session pour changer les exclusions.",
            )
        if body.user1 == body.user2:
            raise HTTPException(
                status_code=422, detail="Choisis deux membres différents pour former une paire."
            )
        return session

    @router.post("/api/servers/{server_id}/secretsanta/exclusions")
    async def api_secretsanta_exclude(request: Request, server_id: str, body: PairRequest):
        session = await _exclusion_guard(request, server_id, body)
        if not await SecretSantaRepository().exclude_pair(
            f"guild_{server_id}", int(body.user1), int(body.user2)
        ):
            raise HTTPException(status_code=409, detail="Cette paire est déjà exclue.")
        await audit.record(server_id, session, "secretsanta.exclude")
        return JSONResponse({"exclusions": await _exclusions(server_id)})

    @router.post("/api/servers/{server_id}/secretsanta/exclusions/remove")
    async def api_secretsanta_allow(request: Request, server_id: str, body: PairRequest):
        session = await _exclusion_guard(request, server_id, body)
        if not await SecretSantaRepository().allow_pair(
            f"guild_{server_id}", int(body.user1), int(body.user2)
        ):
            raise HTTPException(
                status_code=404, detail="Cette paire n'est pas exclue : recharge la page."
            )
        await audit.record(server_id, session, "secretsanta.allow")
        return JSONResponse({"exclusions": await _exclusions(server_id)})

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

    @router.post("/api/servers/{server_id}/secretsanta/reveal")
    async def api_secretsanta_reveal(request: Request, server_id: str):
        session = await ctx.require_guild_admin(request, server_id)
        if not await _is_administrator(ctx, session, server_id):
            raise HTTPException(
                status_code=403,
                detail="Seul un administrateur du serveur peut révéler les attributions.",
            )
        santa = await _session(server_id)
        if santa is None:
            raise HTTPException(status_code=404, detail="Aucune session de Père Noël Secret.")
        pairs = await SecretSantaRepository().get_draw_results(santa.context_id)
        if not santa.is_drawn or not pairs:
            raise HTTPException(status_code=409, detail="Le tirage n'a pas encore été fait.")
        ids = list(dict.fromkeys(uid for pair in pairs for uid in pair))
        names = await ctx.call_bot(
            member_names, ctx.bot, server_id, ids, what=f"name Secret Santa pairs of {server_id}"
        )
        logger.info(
            "Secret Santa revealed via WebUI for guild %s (by %s/%s)",
            server_id,
            session.username,
            session.user_id,
        )
        await audit.record(server_id, session, "secretsanta.reveal", details={"pairs": len(pairs)})
        return JSONResponse(
            {
                "pairs": [
                    {"giver": names.get(str(g), str(g)), "receiver": names.get(str(r), str(r))}
                    for g, r in pairs
                ]
            }
        )

    return router
