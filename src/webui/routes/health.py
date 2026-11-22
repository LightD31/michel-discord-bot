"""A guild's "Santé" card: channels the bot can't use, failing feeds, task runs (F08)."""

from functools import partial

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from features.rss import RssRepository
from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.core.health import health
from src.webui import schemas
from src.webui.context import WebUIContext
from src.webui.guild_health import (
    channel_report,
    configured_channels,
    guild_tasks,
    rss_report,
    task_report,
)

logger = logutil.init_logger("webui.routes.health")

RSS_MODULE = "moduleRss"


def _models() -> dict:
    found = {}
    for key in schemas.MODULE_SCHEMAS:
        model = schemas.module_model(key)
        if model is not None:
            found[key] = model
    return found


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/health")
    async def api_guild_health(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        server = ctx.get_full_config().get("servers", {}).get(server_id)
        if not isinstance(server, dict):
            raise HTTPException(status_code=404, detail="Serveur non trouvé")
        enabled = {
            key for key, value in server.items() if isinstance(value, dict) and value.get("enabled")
        }

        channels = None  # None: no bot to ask (a dashboard run on its own)
        tasks: list = []
        if ctx.bot is not None:
            # Where the bot posts: the channels a list names are ones it leaves alone.
            wanted = configured_channels(
                server, _models(), partial(schemas.channel_ids, lists=False)
            )
            channels = await ctx.call_bot(
                channel_report,
                ctx.bot,
                server_id,
                wanted,
                server,
                what=f"check channels of {server_id}",
            )
            tasks = task_report(guild_tasks(ctx.bot, enabled), health.snapshot())

        feeds = None
        if RSS_MODULE in enabled:
            try:
                states = await RssRepository(server_id).states()
            except DatabaseError as e:
                logger.warning("RSS states of %s unreadable: %s", server_id, e)
            else:
                feeds = rss_report(server.get(RSS_MODULE) or {}, states)

        return JSONResponse({"channels": channels, "feeds": feeds, "tasks": tasks})

    return router
