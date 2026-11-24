"""A module's earlier settings, and putting one back.

See :mod:`src.webui.config_history`. A restore is a save: it goes through
:func:`save_module_settings`, so the channels and roles it brings back are
checked again, the module's current secrets are kept, and the settings it
replaces become a version in turn.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.core import logging as logutil
from src.webui import schemas
from src.webui.config_history import ConfigHistoryRepository, version_to_json, without_secrets
from src.webui.context import WebUIContext
from src.webui.routes.servers import save_module_settings
from src.webui.secrets import SECRET_PLACEHOLDER, secret_field_names

logger = logutil.init_logger("webui.routes.config_history")


def _known_module(module_name: str) -> None:
    if module_name not in schemas.MODULE_SCHEMAS:
        raise HTTPException(status_code=404, detail="Module inconnu")


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    def current_settings(server_id: str, module_name: str) -> dict:
        server = ctx.get_full_config().get("servers", {}).get(server_id)
        settings = server.get(module_name) if isinstance(server, dict) else None
        return settings if isinstance(settings, dict) else {}

    @router.get("/api/servers/{server_id}/modules/{module_name}/history")
    async def api_history(request: Request, server_id: str, module_name: str):
        """The module's earlier versions, newest first, each with what the next save changed."""
        await ctx.require_guild_admin(request, server_id)
        _known_module(module_name)
        schema = schemas.MODULE_SCHEMAS.get(module_name)
        versions = await ConfigHistoryRepository(server_id).list(module_name)
        newer = without_secrets(current_settings(server_id, module_name), schema)
        out = []
        for version in versions:
            out.append(version_to_json(version, newer))
            settings = version.get("settings")
            newer = settings if isinstance(settings, dict) else {}
        return JSONResponse({"versions": out})

    @router.post("/api/servers/{server_id}/modules/{module_name}/history/{version_id}/restore")
    async def api_restore(request: Request, server_id: str, module_name: str, version_id: str):
        """Save the module's settings as they were in *version_id*, keeping today's secrets."""
        session = await ctx.require_guild_admin(request, server_id)
        _known_module(module_name)
        version = await ConfigHistoryRepository(server_id).get(module_name, version_id)
        if version is None or not isinstance(version.get("settings"), dict):
            raise HTTPException(status_code=404, detail="Version introuvable")
        settings = dict(version["settings"])
        # The secrets were never stored: the placeholder keeps the current
        # ones, and restore_section refuses one bound to a URL the version changes.
        current = current_settings(server_id, module_name)
        for name in secret_field_names(schemas.MODULE_SCHEMAS.get(module_name)):
            if current.get(name):
                settings[name] = SECRET_PLACEHOLDER
        after = await save_module_settings(
            ctx,
            server_id,
            module_name,
            settings,
            session,
            action="module.restore",
            details={"version": version["at"].isoformat() if version.get("at") else version_id},
        )
        return JSONResponse({"status": "ok", **after})

    return router
