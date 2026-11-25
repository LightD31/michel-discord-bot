"""The monitors of a server's own Uptime Kuma, for its alerts' picker (P42).

``GET /api/servers/{id}/uptime/monitors`` (guild admin) reads ``/metrics``
with the server's saved URL and key, as the bot does, and gives each
monitor's id, name and status. The key never reaches the browser.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core import logging as logutil
from src.integrations.uptime_kuma import UptimeKumaError, UptimeKumaSettings, fetch_monitor_states
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.uptime")


def _saved_settings(ctx: WebUIContext, server_id: str) -> dict[str, Any]:
    """The module's saved settings, even while it is off (alerts are set before)."""
    server = ctx.get_full_config().get("servers", {}).get(server_id) or {}
    module = server.get("moduleUptime") if isinstance(server, dict) else None
    return module if isinstance(module, dict) else {}


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/uptime/monitors")
    async def api_uptime_monitors(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        kuma = UptimeKumaSettings.from_section(_saved_settings(ctx, server_id))
        if not kuma.metrics_configured:
            raise HTTPException(
                status_code=422,
                detail="Enregistre d'abord l'URL et la clé API d'Uptime Kuma du serveur.",
            )
        try:
            states = await fetch_monitor_states(kuma)
        except UptimeKumaError as e:
            logger.info("Uptime Kuma monitors of %s unreadable: %s", server_id, e)
            raise HTTPException(
                status_code=502, detail="Uptime Kuma ne répond pas ou refuse la clé d'API."
            ) from e
        monitors = sorted(states.values(), key=lambda m: m.name.lower())
        return JSONResponse(
            {
                "monitors": [
                    {
                        "id": str(m.id),
                        "name": m.name,
                        "status": int(m.status) if m.status is not None else None,
                    }
                    for m in monitors
                ]
            }
        )

    return router
