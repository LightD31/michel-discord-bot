"""A guild's members for the dashboard's pickers: one search page, or a few by id.

``GET /api/servers/{id}/members?q=<texte>&limit=&offset=`` answers one page of
the members matching the search, with the total; ``?ids=1,2,3`` resolves the
members a form already references, to label them. The list itself is built on
the bot loop and cached a minute per guild (:mod:`src.webui.members`).
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from src.core.config import load_discord2name
from src.webui.context import WebUIContext
from src.webui.members import (
    DEFAULT_PAGE,
    MAX_IDS,
    MAX_PAGE,
    Member,
    MemberDirectory,
    load_members,
    pick,
    search,
)


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    async def _load(guild_id: str) -> list[Member]:
        return await ctx.call_bot(
            load_members, ctx.bot, guild_id, what=f"list members of guild {guild_id}"
        )

    directory = MemberDirectory(_load)

    @router.get("/api/servers/{server_id}/members")
    async def api_get_server_members(
        request: Request,
        server_id: str,
        q: str = Query("", max_length=100),
        ids: str = Query("", max_length=MAX_IDS * 21),
        limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
        offset: int = Query(0, ge=0),
    ):
        await ctx.require_guild_admin(request, server_id)
        ctx.require_bot_loop()
        members = await directory.members(server_id)
        if ids:
            wanted = [i.strip() for i in ids.split(",") if i.strip().isdigit()][:MAX_IDS]
            return JSONResponse({"members": [m.to_dict() for m in pick(members, wanted)]})
        page, total = search(
            members, q, names=load_discord2name(server_id), limit=limit, offset=offset
        )
        return JSONResponse(
            {
                "members": [m.to_dict() for m in page],
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        )

    return router
