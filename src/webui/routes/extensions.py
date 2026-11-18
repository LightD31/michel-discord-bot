"""Extension management routes: list, toggle, reload."""

import ast
import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.core import logging as logutil
from src.core.logging import log_failure
from src.webui import audit
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


# The extensions that own no module of the dashboard: what the Extensions page
# calls them. Each other one is named after the module it registers.
WITHOUT_MODULE: dict[str, tuple[str, str, str]] = {
    "extensions.backup": ("💾", "Sauvegardes", "Sauvegarde MongoDB planifiée et /backup."),
    "extensions.datapurge": (
        "🧹",
        "Purge des données",
        "Efface les données d'un serveur 30 jours après le départ du bot.",
    ),
    "extensions.errors": (
        "🚨",
        "Erreurs",
        "Répond aux membres quand une commande, un bouton ou un formulaire échoue.",
    ),
    "extensions.help": ("❓", "Aide", "/aide : les commandes disponibles sur chaque serveur."),
    "extensions.metrics": ("📈", "Métriques", "Exporte les métriques Prometheus du bot."),
    "extensions.mydata": (
        "🔐",
        "Mes données",
        "/mes-donnees : un membre exporte ou supprime ses données.",
    ),
    "extensions.polls": ("📊", "Sondages", "/poll : sondages à boutons (module Utilitaires)."),
    "extensions.profile": (
        "🪪",
        "Profil",
        "/profil et « 📊 Profil » : la carte d'un membre (module Système d'XP).",
    ),
    "extensions.random_": (
        "🎲",
        "Hasard",
        "Tirage au sort, dés, pile ou face et mélange de listes.",
    ),
    "extensions.reminders": ("⏰", "Rappels", "/reminder : rappels par MP (module Utilitaires)."),
    "extensions.userinfo": (
        "👤",
        "Membres",
        "Garde les noms des membres à jour pour le dashboard et les classements.",
    ),
}


def _source_files(ext_path: str) -> list[Path]:
    base = Path(EXTENSIONS_DIR) / ext_path.removeprefix("extensions.")
    if base.is_dir():
        return sorted(base.glob("*.py"))
    file = base.with_suffix(".py")
    return [file] if file.is_file() else []


def _registered_module(tree: ast.AST) -> dict[str, str] | None:
    """The ``__icon__`` / ``__label__`` / ``__description__`` of a ``@register_module`` class."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        decorators = [d.func for d in node.decorator_list if isinstance(d, ast.Call)]
        if not any(getattr(d, "id", None) == "register_module" for d in decorators):
            continue
        found = {}
        for stmt in node.body:
            if (
                isinstance(stmt, (ast.Assign, ast.AnnAssign))
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = stmt.value.value
        return found
    return None


@lru_cache(maxsize=128)
def extension_card(ext_path: str) -> dict[str, str]:
    """How the Extensions page names *ext_path*: an icon, a label, a description.

    Read from the sources, without importing anything: the module the
    extension registers for the dashboard, else :data:`WITHOUT_MODULE`, else
    its file name.
    """
    if ext_path in WITHOUT_MODULE:
        icon, label, description = WITHOUT_MODULE[ext_path]
        return {"icon": icon, "label": label, "description": description}
    for file in _source_files(ext_path):
        try:
            module = _registered_module(ast.parse(file.read_text(encoding="utf-8")))
        except (OSError, SyntaxError, ValueError):
            continue
        if module and module.get("__label__"):
            return {
                "icon": module.get("__icon__", "📦"),
                "label": module["__label__"],
                "description": module.get("__description__", ""),
            }
    return {"icon": "📦", "label": ext_path.removeprefix("extensions."), "description": ""}


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
        session = ctx.require_developer(request)
        if not ctx.bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")

        results: dict[str, list] = {"reloaded": [], "failed": []}
        for ext_path in ctx.get_extension_module_paths():
            try:
                await run_extension_op(ctx, "reload", ext_path)
                results["reloaded"].append(ext_path)
                logger.info(
                    "Reloaded extension %s for %s (%s)", ext_path, session.username, session.user_id
                )
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
        await audit.record(
            None,
            session,
            "extensions.reload_all",
            details={"reloaded": len(results["reloaded"]), "failed": len(results["failed"])},
        )
        return JSONResponse(
            {**results, "commandSync": {"synced": not sync_error, "error": sync_error}}
        )

    @router.post("/api/reload/{ext_name:path}")
    async def api_reload_one(request: Request, ext_name: str):
        """Reload a single extension by module path (e.g. ``extensions.tricount``)."""
        session = ctx.require_developer(request)
        if not ctx.bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")
        _require_known_extension(ext_name)
        try:
            await run_extension_op(ctx, "reload", ext_name)
            logger.info(
                "Reloaded extension %s for %s (%s)", ext_name, session.username, session.user_id
            )
        except HTTPException:
            raise
        except Exception as e:
            log_failure(logger, f"Failed to reload {ext_name}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Échec du rechargement ({type(e).__name__}). Voir les logs.",
            ) from e
        sync_error = await _sync_all_commands()
        await audit.record(None, session, "extensions.reload", target=ext_name)
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
                **extension_card(ext_path),
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

        await audit.record(
            None,
            session,
            "extensions.toggle",
            target=ext_name,
            details={"enabled": body.enabled, "error": bool(error)},
        )
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
