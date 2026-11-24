"""The agenda's calendar on the server page (P19).

``GET /api/servers/{id}/agenda`` gathers the next days on the bot loop
(:func:`src.discord_ext.agenda.gather_agenda`, as ``/agenda`` does) for the
server's admins, and returns them day by day: names, never member ids.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from features.agenda import bounded_days, day_label, sort_entries
from src.core import clock
from src.core.config import ModuleConfig
from src.discord_ext.agenda import gather_agenda
from src.webui.context import WebUIContext

AGENDA = ModuleConfig("moduleAgenda")


async def agenda_on_bot(bot: Any, server_id: str) -> dict[str, Any]:
    if not AGENDA.enabled(server_id):
        raise HTTPException(status_code=404, detail="L'agenda n'est pas activé sur ce serveur.")
    now = clock.now()
    days = bounded_days(AGENDA.get(server_id).get("agendaDays"))
    agenda = await gather_agenda(bot, server_id, now, days)
    today = clock.to_local(now).date()
    return {
        "days": days,
        "today": today.isoformat(),
        "entries": [
            {
                "day": entry.day.isoformat(),
                "dayLabel": day_label(entry.day, today),
                "kind": entry.kind,
                "icon": entry.icon,
                "title": entry.title,
                "time": f"{clock.to_local(entry.at):%H:%M}" if entry.at else None,
                "url": entry.url,
            }
            for entry in sort_entries(agenda.entries)
        ],
        "unavailable": agenda.unavailable,
    }


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/agenda")
    async def api_agenda(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        data = await ctx.call_bot(agenda_on_bot, ctx.bot, server_id, what=f"agenda of {server_id}")
        return JSONResponse(data)

    return router


__all__ = ["agenda_on_bot", "create_router"]
