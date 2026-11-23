"""The dashboard's audit log, read back (F09).

A guild's log is open to every admin of that guild, so co-admins see each
other's changes; the bot's own log (global sections, extensions, short links)
to developers only. See :mod:`src.webui.audit`.
"""

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.core import logging as logutil
from src.webui.audit import PAGE_SIZE, AuditRepository, entry_to_json
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.audit")


def _before(value: str | None) -> ObjectId | None:
    if not value:
        return None
    if not ObjectId.is_valid(value):
        raise HTTPException(status_code=422, detail="Paramètre « before » invalide")
    return ObjectId(value)


async def _page(guild_id: str | None, limit: int, before: str | None) -> JSONResponse:
    entries = await AuditRepository(guild_id).list(limit=limit, before=_before(before))
    return JSONResponse({"entries": [entry_to_json(e) for e in entries]})


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/audit")
    async def api_guild_audit(
        request: Request,
        server_id: str,
        limit: int = Query(default=PAGE_SIZE, ge=1, le=PAGE_SIZE),
        before: str | None = Query(default=None),
    ):
        await ctx.require_guild_admin(request, server_id)
        return await _page(server_id, limit, before)

    @router.get("/api/audit")
    async def api_bot_audit(
        request: Request,
        limit: int = Query(default=PAGE_SIZE, ge=1, le=PAGE_SIZE),
        before: str | None = Query(default=None),
    ):
        ctx.require_developer(request)
        return await _page(None, limit, before)

    return router
