"""Extension management routes: list, toggle, reload."""

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.core import logging as logutil
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.extensions")


class ExtensionToggle(BaseModel):
    enabled: bool


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.post("/api/reload")
    async def api_reload_all(request: Request):
        """Reload all extensions to apply config changes."""
        ctx.require_developer(request)
        if not ctx.bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")

        results: dict[str, list] = {"reloaded": [], "failed": []}
        for ext_path in ctx.get_extension_module_paths():
            try:
                ctx.bot.reload_extension(ext_path)
                results["reloaded"].append(ext_path)
                logger.info(f"Reloaded extension: {ext_path}")
            except Exception as e:
                results["failed"].append({"name": ext_path, "error": str(e)})
                logger.error(f"Failed to reload {ext_path}: {e}")
        return JSONResponse(results)

    @router.post("/api/reload/{ext_name:path}")
    async def api_reload_one(request: Request, ext_name: str):
        """Reload a single extension by module path (e.g. ``extensions.tricount``)."""
        ctx.require_developer(request)
        if not ctx.bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")
        try:
            ctx.bot.reload_extension(ext_name)
            logger.info(f"Reloaded extension: {ext_name}")
            return JSONResponse({"status": "ok", "extension": ext_name})
        except Exception as e:
            logger.error(f"Failed to reload {ext_name}: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e

    @router.get("/api/extensions")
    async def api_list_extensions(request: Request):
        """List all available extensions with their enabled/loaded status."""
        ctx.require_developer(request)
        data = ctx.get_full_config()
        ext_config = data.get("config", {}).get("extensions", {})

        loaded_exts = set(ctx.get_extension_module_paths()) if ctx.bot else set()

        result = []
        ext_dir = "extensions"
        if os.path.isdir(ext_dir):
            for fname in sorted(os.listdir(ext_dir)):
                if fname.startswith("_") or fname.startswith("__"):
                    continue
                full_path = os.path.join(ext_dir, fname)
                if fname.endswith(".py") and os.path.isfile(full_path):
                    ext_path = f"extensions.{fname[:-3]}"
                    short_name = fname[:-3]
                elif os.path.isdir(full_path) and os.path.isfile(
                    os.path.join(full_path, "__init__.py")
                ):
                    ext_path = f"extensions.{fname}"
                    short_name = fname
                else:
                    continue
                default_enabled = not short_name.startswith("_")
                enabled = ext_config.get(ext_path, default_enabled)
                result.append(
                    {
                        "path": ext_path,
                        "filename": fname,
                        "enabled": enabled,
                        "loaded": ext_path in loaded_exts,
                    }
                )
        return JSONResponse({"extensions": result})

    @router.post("/api/extensions/{ext_name:path}/toggle")
    async def api_toggle_extension(request: Request, ext_name: str, body: ExtensionToggle):
        """Enable or disable an extension globally (updates config and loads/unloads)."""
        ctx.require_developer(request)
        data = ctx.get_full_config()
        data.setdefault("config", {}).setdefault("extensions", {})[ext_name] = body.enabled
        ctx.save_config(data)
        logger.info(f"{'Enabled' if body.enabled else 'Disabled'} extension {ext_name} in config")

        loaded = ext_name in set(ctx.get_extension_module_paths()) if ctx.bot else False
        error = None
        if ctx.bot:
            try:
                if body.enabled:
                    ctx.bot.load_extension(ext_name)
                    loaded = True
                    logger.info(f"Loaded extension: {ext_name}")
                else:
                    ctx.bot.unload_extension(ext_name)
                    loaded = False
                    logger.info(f"Unloaded extension: {ext_name}")
            except Exception as e:
                error = str(e)
                logger.error(f"Failed to {'load' if body.enabled else 'unload'} {ext_name}: {e}")
                loaded = ext_name in set(ctx.get_extension_module_paths())

        return JSONResponse(
            {
                "status": "ok",
                "path": ext_name,
                "enabled": body.enabled,
                "loaded": loaded,
                "error": error,
            }
        )

    return router
