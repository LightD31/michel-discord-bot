"""Infraction browser endpoints for the WebUI moderation view, and the reports.

Read-only listing plus a revoke action. ``GET …/reports`` (P15) lists the
messages members reported, for the moderators (``MODERATE_MEMBERS``): it
names the reporters, which only moderators see on the mod-log cards too. The
cards' buttons stay the place to act. Revoking flips a case to inactive in
MongoDB and posts a best-effort note to the guild's modlog channel via the bot
loop (mirroring how ``routes/rolemenus.py`` calls back into Discord).
"""

from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from features.moderation import Infraction, InfractionType, ModerationRepository
from features.moderation.reports import MessageReport, ReportRepository
from src.core import logging as logutil
from src.core.permissions import ADMINISTRATOR, MODERATE_MEMBERS, has_any
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.mentions import NO_PINGS
from src.webui import audit
from src.webui.auth import Session
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


def _report_to_dict(server_id: str, report: MessageReport) -> dict:
    def link(channel_id: str | None, message_id: str | None) -> str | None:
        if not channel_id or not message_id:
            return None
        return f"https://discord.com/channels/{server_id}/{channel_id}/{message_id}"

    return {
        "message_id": report.message_id,
        "channel_id": report.channel_id,
        "author_id": report.author_id,
        "status": report.status,
        "handled_by": report.handled_by,
        "handled_at": report.handled_at.isoformat() if report.handled_at else None,
        "created_at": report.created_at.isoformat(),
        "reporters": [
            {"user_id": r.user_id, "reason": r.reason, "at": r.at.isoformat()}
            for r in report.reporters
        ],
        "message_url": link(report.channel_id, report.message_id),
        "card_url": link(report.card_channel_id, report.card_message_id),
    }


async def _require_moderator(
    ctx: WebUIContext, session: Session, server_id: str, *, to: str = "révoquer"
) -> None:
    """403 unless the user could lift a sanction in Discord: ``MODERATE_MEMBERS``.

    The dashboard bar (``MANAGE_GUILD``) is a settings permission; revoking a
    case is a moderation act. Read live from the bot like the bar itself; a
    dashboard without a bot only has the login snapshot.
    """
    if ctx.oauth.is_developer(session):
        return
    if ctx.bot is None:
        entry: dict = next(
            (g for g in session.guilds if isinstance(g, dict) and str(g.get("id")) == server_id),
            {},
        )
        permissions, owner = int(entry.get("permissions") or 0), bool(entry.get("owner"))
    else:
        access = await ctx.guild_access(session, server_id)
        permissions, owner = access.permissions, access.is_owner
    if not (owner or has_any(permissions, ADMINISTRATOR | MODERATE_MEMBERS)):
        raise HTTPException(
            status_code=403,
            detail=f"Il faut la permission « Exclure temporairement des membres » pour {to}.",
        )


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
        await ctx.require_guild_admin(request, server_id)
        active_filter: bool | None = None
        if active in ("true", "1"):
            active_filter = True
        elif active in ("false", "0"):
            active_filter = False
        repo = ModerationRepository(server_id)
        items = await repo.list(
            type=cast(InfractionType | None, type or None),
            user_id=user_id or None,
            active=active_filter,
            limit=500,
        )
        return JSONResponse({"infractions": [_infraction_to_dict(i) for i in items]})

    @router.get("/api/servers/{server_id}/reports")
    async def api_list_reports(
        request: Request, server_id: str, status: str | None = Query(default=None)
    ):
        session = await ctx.require_guild_admin(request, server_id)
        await _require_moderator(ctx, session, server_id, to="voir les signalements")
        items = await ReportRepository(server_id).list(open_only=status == "open")
        return JSONResponse({"reports": [_report_to_dict(server_id, r) for r in items]})

    @router.delete("/api/servers/{server_id}/infractions/{case_id}")
    async def api_revoke_infraction(request: Request, server_id: str, case_id: int):
        session = await ctx.require_guild_admin(request, server_id)
        await _require_moderator(ctx, session, server_id)
        repo = ModerationRepository(server_id)
        existing = await repo.get(case_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Cas introuvable")
        if str(existing.user_id) == str(session.user_id):
            raise HTTPException(
                status_code=403, detail="Tu ne peux pas révoquer un cas qui te vise."
            )
        if not existing.active:
            raise HTTPException(status_code=400, detail="Cas déjà révoqué")
        await repo.set_active(case_id, False)

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
            channel = await fetch_guild_channel(ctx.bot, server_id, chan_id, logger=logger)
            if channel is None or not hasattr(channel, "send"):
                return
            try:
                await channel.send(
                    f"♻️ Cas #{case_id} révoqué via le dashboard par <@{session.user_id}> "
                    f"({session.username}).",
                    allowed_mentions=NO_PINGS,
                )
            except Exception as e:
                logger.warning("Could not post revocation note: %s", e)

        try:
            await ctx.call_bot(_post_note, what=f"post revocation note for guild {server_id}")
        except HTTPException as e:
            # Best effort: the revocation itself is already saved.
            logger.warning("Revocation note dispatch failed: %s", e.detail)

        logger.info(
            "Infraction #%s revoked via WebUI for guild %s (by %s/%s)",
            case_id,
            server_id,
            session.username,
            session.user_id,
        )
        await audit.record(server_id, session, "infraction.revoke", target=str(case_id))
        return JSONResponse({"status": "ok"})

    return router
