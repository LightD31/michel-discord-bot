"""The top of a server page: the week's activity, the latest changes, each module's last edit.

Each module card also says when the module last did its own work in the
background (``lastRuns``, P15): the latest success of its tasks, from the bot's
task health; ``null`` without a bot.

The activity comes from the XP log (``xp_events``), so it is only known where
the XP module is on; the changes from the dashboard's audit log. Each part
that can't be read is ``null`` rather than failing the page.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from features.xp import STATS_TIMEZONE, XpRepository
from src.core import clock
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.core.health import health
from src.webui.audit import AuditRepository, entry_to_json
from src.webui.context import WebUIContext
from src.webui.guild_health import module_last_runs

logger = logutil.init_logger("webui.routes.overview")

ACTIVITY_DAYS = 7
RECENT_CHANGES = 3
MODULE_ACTIONS = ("module.save", "module.toggle", "module.restore")
XP_MODULE = "moduleXp"


async def week_activity(server_id: str) -> dict[str, int]:
    """Members who earned XP, and messages counted, over the last :data:`ACTIVITY_DAYS` days."""
    since = clock.now() - timedelta(days=ACTIVITY_DAYS)
    repo = XpRepository(server_id)
    members = await repo.count_active_users(since)
    rows = await repo.daily_activity(since, STATS_TIMEZONE)
    messages = sum(int(r.get("events", 0)) for r in rows if r.get("source") == "message")
    return {"days": ACTIVITY_DAYS, "activeMembers": members, "messages": messages}


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/overview")
    async def api_overview(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        server = ctx.get_full_config().get("servers", {}).get(server_id)
        xp = server.get(XP_MODULE) if isinstance(server, dict) else None
        out: dict[str, Any] = {
            "activity": None,
            "recent": None,
            "lastChanges": None,
            "lastRuns": None,
        }
        bot = getattr(ctx, "bot", None)
        if bot is not None:
            runs = module_last_runs(bot, health.snapshot())
            out["lastRuns"] = {
                module: datetime.fromtimestamp(at, UTC).isoformat() for module, at in runs.items()
            }
        if isinstance(xp, dict) and xp.get("enabled"):
            try:
                out["activity"] = await week_activity(server_id)
            except DatabaseError as e:
                logger.warning("Activity of %s unreadable: %s", server_id, e)
        log = AuditRepository(server_id)
        try:
            out["recent"] = [entry_to_json(e) for e in await log.list(limit=RECENT_CHANGES)]
            changes = await log.last_changes(MODULE_ACTIONS)
            out["lastChanges"] = {module: at.isoformat() for module, at in changes.items()}
        except DatabaseError as e:
            logger.warning("Audit log of %s unreadable: %s", server_id, e)
        return JSONResponse(out)

    return router
