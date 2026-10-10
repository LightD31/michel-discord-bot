"""AI model endpoints for the dashboard's model picker.

- ``GET /api/ai/catalog?api=…`` (developer): a provider's live model catalog,
  so the global ``AI.models`` list can be filled by picking instead of typing
  ids. Read-only, proxied through :mod:`src.integrations.llm` (memoized there).
- ``GET /api/servers/{id}/ai/models`` (guild admin): the *configured* models'
  keys and names, for the per-guild default-model dropdown. Exposes nothing
  else from the developer-only global config.

Neither touches bot state, so nothing here runs on the bot loop.
"""

from fastapi import APIRouter, HTTPException, Query, Request

from features.ai import AiGlobalSettings
from src.core import logging as logutil
from src.core.config import config_store
from src.integrations import llm
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.ai")


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ai/catalog")
    async def api_ai_catalog(request: Request, api: str = Query(...)):
        ctx.require_developer(request)
        if api not in llm.SUPPORTED_APIS:
            raise HTTPException(status_code=400, detail="API inconnue")
        if not llm.get_api_settings(api).configured:
            raise HTTPException(
                status_code=400,
                detail=f"{llm.API_LABELS[api]} non configurée (clé API et URL requises)",
            )
        try:
            models = await llm.fetch_catalog(api)
        except llm.LlmError as e:
            logger.warning("Catalog fetch failed for %s: %s", api, e)
            raise HTTPException(
                status_code=502, detail=f"Catalogue {llm.API_LABELS[api]} indisponible"
            ) from e
        return {
            "api": api,
            "models": [m.to_dict() for m in sorted(models, key=lambda m: m.name.lower())],
        }

    @router.get("/api/ai/apis")
    async def api_ai_apis(request: Request):
        ctx.require_developer(request)
        configured = set(llm.configured_apis())
        return {
            "apis": [
                {"id": name, "label": llm.API_LABELS[name], "configured": name in configured}
                for name in llm.SUPPORTED_APIS
            ]
        }

    @router.get("/api/servers/{server_id}/ai/models")
    async def api_ai_guild_models(request: Request, server_id: str):
        ctx.require_guild_admin(request, server_id)
        settings = AiGlobalSettings.from_config(config_store.get().get("config", {}))
        return {
            "models": [
                {"key": m.key, "display_name": m.display_name} for m in settings.models.values()
            ],
            "default": settings.default_model,
        }

    return router
