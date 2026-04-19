"""Bot status + introspection endpoint."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.webui.context import WebUIContext, build_module_to_extension_map


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/bot-info")
    async def api_bot_info(request: Request):
        """Get bot status information.

        Available to all authenticated users; admins receive extra details.
        """
        session = ctx.require_session(request)
        is_admin = ctx.is_admin_user(session)
        info: dict = {"status": "unknown", "guilds": 0}
        if ctx.bot:
            try:
                info["status"] = "online" if ctx.bot.is_ready else "starting"
                info["guilds"] = len(ctx.bot.guilds) if ctx.bot.guilds else 0
                info["user"] = str(ctx.bot.user) if ctx.bot.user else None
                info["latency"] = round(ctx.bot.latency * 1000) if ctx.bot.latency else None
                if is_admin:
                    info["extensions"] = ctx.get_extension_module_paths()
                    info["module_extension_map"] = build_module_to_extension_map()
                    info["bot_guild_ids"] = (
                        [str(g.id) for g in ctx.bot.guilds] if ctx.bot.guilds else []
                    )
            except Exception:
                info["status"] = "error"
        return JSONResponse(info)

    return router
