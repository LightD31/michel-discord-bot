"""Extension management routes: list, toggle, reload."""

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.core import logging as logutil
from src.core.logging import log_failure
from src.webui.botops import ExtensionAction, run_extension_op, sync_commands
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.extensions")


EXTENSIONS_DIR = "extensions"


class ExtensionToggle(BaseModel):
    enabled: bool


def available_extensions(ext_dir: str = EXTENSIONS_DIR) -> list[tuple[str, str]]:
    """``(module path, file name)`` of every extension the dashboard can manage.

    Same discovery as ``main.py``: a ``.py`` file or a package under
    ``extensions/``. Names starting with ``_`` are parked work in progress and
    are not offered.
    """
    found: list[tuple[str, str]] = []
    if not os.path.isdir(ext_dir):
        return found
    for fname in sorted(os.listdir(ext_dir)):
        if fname.startswith("_"):
            continue
        full_path = os.path.join(ext_dir, fname)
        if fname.endswith(".py") and os.path.isfile(full_path):
            found.append((f"extensions.{fname[:-3]}", fname))
        elif os.path.isdir(full_path) and os.path.isfile(os.path.join(full_path, "__init__.py")):
            found.append((f"extensions.{fname}", fname))
    return found


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    def _require_known_extension(ext_name: str) -> None:
        """404 unless *ext_name* is an extension on disk or one that is loaded.

        The name ends up in ``config.extensions`` and in an import: anything
        else would write junk keys to config.json and import arbitrary modules.
        """
        known = {path for path, _ in available_extensions()}
        known.update(ctx.get_extension_module_paths())
        if ext_name not in known:
            raise HTTPException(status_code=404, detail="Extension inconnue")

    async def _sync_all_commands() -> str | None:
        """Push the reloaded command set to Discord. Returns an error message, or None.

        A failed sync doesn't invalidate the reload itself, so it's reported
        alongside the result rather than raised.
        """
        try:
            await sync_commands(ctx)
        except TimeoutError:
            logger.error("Command sync timed out after an extension change")
            return "Synchronisation des commandes trop longue"
        except Exception as e:
            log_failure(logger, "Command sync failed after an extension change: %s", e)
            return f"Échec de la synchronisation ({type(e).__name__}). Voir les logs."
        logger.info("Synced slash commands after an extension change")
        return None

    @router.post("/api/reload")
    async def api_reload_all(request: Request):
        """Reload all extensions to apply config changes."""
        ctx.require_developer(request)
        if not ctx.bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")

        results: dict[str, list] = {"reloaded": [], "failed": []}
        for ext_path in ctx.get_extension_module_paths():
            try:
                await run_extension_op(ctx, "reload", ext_path)
                results["reloaded"].append(ext_path)
                logger.info(f"Reloaded extension: {ext_path}")
            except Exception as e:
                results["failed"].append({"name": ext_path, "error": type(e).__name__})
                log_failure(logger, f"Failed to reload {ext_path}: {e}")

        # One sync for the whole pass, and only once every extension is back:
        # the client deletes commands it no longer knows about, so syncing
        # around a failed reload would drop that extension's commands from
        # Discord.
        sync_error = None
        if results["failed"]:
            sync_error = "Synchronisation ignorée : au moins une extension n'a pas rechargé"
            logger.warning("Skipping command sync: %d extension(s) failed", len(results["failed"]))
        else:
            sync_error = await _sync_all_commands()
        return JSONResponse(
            {**results, "commandSync": {"synced": not sync_error, "error": sync_error}}
        )

    @router.post("/api/reload/{ext_name:path}")
    async def api_reload_one(request: Request, ext_name: str):
        """Reload a single extension by module path (e.g. ``extensions.tricount``)."""
        ctx.require_developer(request)
        if not ctx.bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")
        _require_known_extension(ext_name)
        try:
            await run_extension_op(ctx, "reload", ext_name)
            logger.info(f"Reloaded extension: {ext_name}")
        except HTTPException:
            raise
        except Exception as e:
            log_failure(logger, f"Failed to reload {ext_name}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Échec du rechargement ({type(e).__name__}). Voir les logs.",
            ) from e
        sync_error = await _sync_all_commands()
        return JSONResponse(
            {
                "status": "ok",
                "extension": ext_name,
                "commandSync": {"synced": not sync_error, "error": sync_error},
            }
        )

    @router.get("/api/extensions")
    async def api_list_extensions(request: Request):
        """List all available extensions with their enabled/loaded status."""
        ctx.require_developer(request)
        data = ctx.get_full_config()
        ext_config = data.get("config", {}).get("extensions", {})

        loaded_exts = set(ctx.get_extension_module_paths()) if ctx.bot else set()

        # Underscore-prefixed entries aren't listed, so every listed one is
        # enabled unless the config says otherwise (same default as main.py).
        result = [
            {
                "path": ext_path,
                "filename": fname,
                "enabled": ext_config.get(ext_path, True),
                "loaded": ext_path in loaded_exts,
            }
            for ext_path, fname in available_extensions()
        ]
        return JSONResponse({"extensions": result})

    @router.post("/api/extensions/{ext_name:path}/toggle")
    async def api_toggle_extension(request: Request, ext_name: str, body: ExtensionToggle):
        """Enable or disable an extension globally (updates config and loads/unloads)."""
        session = ctx.require_developer(request)
        _require_known_extension(ext_name)

        def mutator(data: dict) -> None:
            data.setdefault("config", {}).setdefault("extensions", {})[ext_name] = body.enabled

        ctx.mutate_config(mutator)
        logger.info(
            "%s extension %s in config (by %s/%s)",
            "Enabled" if body.enabled else "Disabled",
            ext_name,
            session.username,
            session.user_id,
        )

        loaded = ext_name in set(ctx.get_extension_module_paths()) if ctx.bot else False
        error = None
        command_sync = None  # stays None when no sync was attempted
        if ctx.bot:
            action: ExtensionAction = "load" if body.enabled else "unload"
            try:
                await run_extension_op(ctx, action, ext_name)
                loaded = body.enabled
                logger.info("%sed extension: %s", action.capitalize(), ext_name)
            except Exception as e:
                error = f"Échec ({type(e).__name__}). Voir les logs."
                log_failure(logger, f"Failed to {action} {ext_name}: {e}")
                loaded = ext_name in set(ctx.get_extension_module_paths())
            else:
                # Toggling an extension adds or removes its commands, so this
                # is the one reload path that always needs Discord told.
                sync_error = await _sync_all_commands()
                command_sync = {"synced": not sync_error, "error": sync_error}

        return JSONResponse(
            {
                "status": "ok",
                "path": ext_name,
                "enabled": body.enabled,
                "loaded": loaded,
                "error": error,
                "commandSync": command_sync,
            }
        )

    return router
