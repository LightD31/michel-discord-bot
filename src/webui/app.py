"""
FastAPI application for the Web UI dashboard.
Provides config management endpoints and serves the frontend.
"""

import asyncio
import json
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from src import logutil
from src.config_manager import load_config as bot_load_config
from src.webui.auth import DiscordOAuth, Session
from src.webui.log_handler import WebUILogHandler, install_log_handler
from src.webui.schemas import MODULE_SCHEMAS, GLOBAL_CONFIG_SCHEMAS

logger = logutil.init_logger("webui.app")

# ── Pydantic models ──────────────────────────────────────────────────

class ModuleToggle(BaseModel):
    module: str
    enabled: bool

class ConfigUpdate(BaseModel):
    config: dict

class GlobalConfigUpdate(BaseModel):
    section: str
    config: dict

# ── App factory ──────────────────────────────────────────────────────

def create_app(bot=None) -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Args:
        bot: The interactions.py Client instance (optional, for live data).
    """
    # Load bot config to get OAuth credentials
    config, _, _ = bot_load_config()
    
    webui_config = config.get("webui", {})
    discord_config = config.get("discord", {})
    
    client_id = webui_config.get("clientId") or discord_config.get("clientId", "")
    client_secret = webui_config.get("clientSecret", "")
    base_url = webui_config.get("baseUrl", "http://localhost:8080")
    redirect_uri = f"{base_url}/auth/callback"
    admin_user_ids = webui_config.get("adminUserIds", [])
    oauth = DiscordOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        admin_user_ids=admin_user_ids,
    )

    # Install log handler on first app creation
    log_handler = WebUILogHandler.get_instance()
    if not log_handler:
        log_handler = install_log_handler(max_entries=2000)

    app = FastAPI(title="Michel Bot Dashboard", docs_url=None, redoc_url=None)

    # ── Helpers ──────────────────────────────────────────────────────

    COOKIE_NAME = "michel_session"

    CONFIG_PATH = os.path.join("config", "config.json")

    def _get_full_config() -> dict:
        """Load the full config from disk."""
        from src.config_manager import load_full_config
        return load_full_config() or {"config": {}, "servers": {}}

    def _save_config(data: dict):
        """Save the full config to a single config.json file."""
        os.makedirs("config", exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def _get_session(request: Request) -> Optional[Session]:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        return oauth.get_session(token)

    def _require_session(request: Request) -> Session:
        session = _get_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="Non authentifié")
        return session

    def _require_admin(request: Request) -> Session:
        session = _require_session(request)
        if not oauth.is_admin(session):
            raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
        return session

    # ── List known modules from extensions ───────────────────────────

    def _discover_modules() -> list[str]:
        """Discover all module names used by extensions."""
        modules = set()
        ext_dir = "extensions"
        if os.path.isdir(ext_dir):
            for fname in os.listdir(ext_dir):
                if fname.endswith(".py") and not fname.startswith("_"):
                    fpath = os.path.join(ext_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        import re
                        for match in re.finditer(r'load_config\(["\']([\w]+)["\']\)', content):
                            modules.add(match.group(1))
                    except Exception:
                        pass
        return sorted(modules)

    def _build_module_to_extension_map() -> dict[str, str]:
        """Build a mapping from module config name to extension module path.
        E.g. {'moduleTricount': 'extensions.tricount', 'moduleTwitch': 'extensions.twitchextv2'}
        """
        mapping = {}
        ext_dir = "extensions"
        if os.path.isdir(ext_dir):
            for fname in os.listdir(ext_dir):
                if fname.endswith(".py") and not fname.startswith("_"):
                    ext_module_path = f"extensions.{fname[:-3]}"
                    fpath = os.path.join(ext_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        import re
                        for match in re.finditer(r'load_config\(["\']([\w]+)["\']\)', content):
                            mod_name = match.group(1)
                            mapping[mod_name] = ext_module_path
                    except Exception:
                        pass
        return mapping

    # ── Auth routes ──────────────────────────────────────────────────

    @app.get("/auth/login")
    async def auth_login():
        """Redirect to Discord OAuth2 login."""
        state = secrets.token_urlsafe(16)
        url = oauth.get_oauth_url(state)
        response = RedirectResponse(url=url)
        response.set_cookie("oauth_state", state, httponly=True, max_age=300)
        return response

    @app.get("/auth/callback")
    async def auth_callback(request: Request, code: str = "", state: str = ""):
        """Handle Discord OAuth2 callback."""
        if not code:
            raise HTTPException(status_code=400, detail="Code manquant")

        session = await oauth.exchange_code(code)
        if not session:
            return HTMLResponse(
                content="<h1>Accès refusé</h1><p>Vous n'êtes pas autorisé à accéder au dashboard.</p>",
                status_code=403,
            )

        response = RedirectResponse(url="/")
        response.set_cookie(
            COOKIE_NAME,
            session.session_token,
            httponly=True,
            max_age=86400,
            samesite="lax",
        )
        response.delete_cookie("oauth_state")
        return response

    @app.get("/auth/logout")
    async def auth_logout(request: Request):
        """Logout and invalidate session."""
        token = request.cookies.get(COOKIE_NAME)
        if token:
            oauth.invalidate_session(token)
        response = RedirectResponse(url="/")
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/api/me")
    async def api_me(request: Request):
        """Get current user info."""
        session = _get_session(request)
        if not session:
            return JSONResponse({"authenticated": False})

        # Only return guilds where the bot is also present
        bot_guild_ids: set[str] = set()
        if bot and bot.guilds:
            bot_guild_ids = {str(g.id) for g in bot.guilds}

        managed = oauth.get_user_managed_guilds(session)
        guilds = [
            {
                "id": g["id"],
                "name": g["name"],
                "icon": g.get("icon"),
                "managed": True,
            }
            for g in managed
            if not bot_guild_ids or g["id"] in bot_guild_ids
        ]

        return JSONResponse({
            "authenticated": True,
            "user_id": session.user_id,
            "username": session.username,
            "avatar": session.avatar,
            "guilds": guilds,
            "is_admin": oauth.is_admin(session),
        })

    # ── Config API routes ────────────────────────────────────────────

    @app.get("/api/config")
    async def api_get_config(request: Request):
        """Get the full configuration."""
        _require_admin(request)
        data = _get_full_config()
        return JSONResponse(data)

    @app.get("/api/modules")
    async def api_get_modules(request: Request):
        """Get all discovered module names with their schemas."""
        _require_admin(request)
        discovered = _discover_modules()
        modules = {}
        for mod_name in discovered:
            schema = MODULE_SCHEMAS.get(mod_name, {})
            modules[mod_name] = {
                "label": schema.get("label", mod_name),
                "description": schema.get("description", ""),
                "icon": schema.get("icon", "🧩"),
                "has_schema": bool(schema),
            }
        return JSONResponse({"modules": modules})

    @app.get("/api/schemas/modules")
    async def api_module_schemas(request: Request):
        """Get all module configuration schemas."""
        _require_admin(request)
        return JSONResponse(MODULE_SCHEMAS)

    @app.get("/api/schemas/modules/{module_name}")
    async def api_module_schema(request: Request, module_name: str):
        """Get schema for a specific module."""
        _require_admin(request)
        schema = MODULE_SCHEMAS.get(module_name)
        if not schema:
            return JSONResponse({"fields": {}, "label": module_name})
        return JSONResponse(schema)

    @app.get("/api/schemas/global")
    async def api_global_schemas(request: Request):
        """Get all global config section schemas."""
        _require_admin(request)
        return JSONResponse(GLOBAL_CONFIG_SCHEMAS)

    @app.get("/api/servers")
    async def api_get_servers(request: Request):
        """Get servers where both the user and the bot are present."""
        session = _require_admin(request)
        data = _get_full_config()
        servers = data.get("servers", {})

        # Build the set of guild IDs the bot is in
        bot_guild_ids: set[str] = set()
        if bot and bot.guilds:
            bot_guild_ids = {str(g.id) for g in bot.guilds}

        # Enrich with guild names from Discord if available
        user_guilds = {g["id"]: g for g in session.guilds}
        result = {}

        # Include servers already in config — only if bot is in them
        for server_id, server_config in servers.items():
            if bot_guild_ids and str(server_id) not in bot_guild_ids:
                continue
            guild_info = user_guilds.get(str(server_id), {})
            result[server_id] = {
                "name": guild_info.get("name", server_config.get("serverName", f"Serveur {server_id}")),
                "icon": guild_info.get("icon"),
                "config": server_config,
            }

        # Also include user's managed guilds that aren't in config yet — only if bot is in them
        for guild_id, guild_info in user_guilds.items():
            if guild_id not in result and (not bot_guild_ids or guild_id in bot_guild_ids):
                result[guild_id] = {
                    "name": guild_info.get("name", f"Serveur {guild_id}"),
                    "icon": guild_info.get("icon"),
                    "config": {},
                }

        return JSONResponse(result)

    @app.get("/api/servers/{server_id}")
    async def api_get_server(request: Request, server_id: str):
        """Get configuration for a specific server."""
        _require_admin(request)
        data = _get_full_config()
        server_config = data.get("servers", {}).get(server_id)
        if server_config is None:
            raise HTTPException(status_code=404, detail="Serveur non trouvé")
        return JSONResponse({"server_id": server_id, "config": server_config})

    @app.put("/api/servers/{server_id}/modules/{module_name}")
    async def api_update_module(request: Request, server_id: str, module_name: str, body: ConfigUpdate):
        """Update a specific module's config for a server."""
        _require_admin(request)
        data = _get_full_config()

        # Create server entry if it doesn't exist
        if server_id not in data.get("servers", {}):
            data.setdefault("servers", {})[server_id] = {}

        data["servers"][server_id][module_name] = body.config
        _save_config(data)
        logger.info(f"Updated {module_name} config for server {server_id}")
        return JSONResponse({"status": "ok"})

    @app.post("/api/servers/{server_id}/modules/{module_name}/toggle")
    async def api_toggle_module(request: Request, server_id: str, module_name: str, body: ModuleToggle):
        """Enable or disable a module for a server."""
        _require_admin(request)
        data = _get_full_config()

        # Create server entry if it doesn't exist
        if server_id not in data.get("servers", {}):
            data.setdefault("servers", {})[server_id] = {}

        if module_name not in data["servers"][server_id]:
            data["servers"][server_id][module_name] = {}

        data["servers"][server_id][module_name]["enabled"] = body.enabled
        _save_config(data)
        logger.info(f"{'Enabled' if body.enabled else 'Disabled'} {module_name} for server {server_id}")
        return JSONResponse({"status": "ok", "enabled": body.enabled})

    @app.get("/api/global-config")
    async def api_get_global_config(request: Request):
        """Get global (non-server-specific) configuration."""
        _require_admin(request)
        data = _get_full_config()
        return JSONResponse(data.get("config", {}))

    @app.put("/api/global-config/{section}")
    async def api_update_global_config(request: Request, section: str, body: GlobalConfigUpdate):
        """Update a section of the global configuration."""
        _require_admin(request)
        data = _get_full_config()
        data.setdefault("config", {})[section] = body.config
        _save_config(data)
        logger.info(f"Updated global config section: {section}")
        return JSONResponse({"status": "ok"})

    # ── Config cleanup ───────────────────────────────────────────────

    @app.post("/api/cleanup-config")
    async def api_cleanup_config(request: Request, dry_run: bool = False):
        """Remove config keys not present in the schemas.

        Query params:
            dry_run: if true, return what would be removed without saving.
        """
        _require_admin(request)
        data = _get_full_config()
        removed: list[dict] = []

        # Clean up per-server module configs
        servers = data.get("servers", {})
        for server_id, server_config in servers.items():
            for module_name, module_config in list(server_config.items()):
                if not isinstance(module_config, dict):
                    continue
                schema = MODULE_SCHEMAS.get(module_name)
                if not schema or not schema.get("fields"):
                    continue
                # directValue modules store raw data, not field-based config
                if schema.get("directValue"):
                    continue
                allowed_keys = set(schema["fields"].keys())
                # Always keep "enabled" even if not explicitly in schema
                allowed_keys.add("enabled")
                for key in list(module_config.keys()):
                    if key not in allowed_keys:
                        removed.append({
                            "location": f"servers.{server_id}.{module_name}",
                            "key": key,
                            "value": module_config[key],
                        })
                        if not dry_run:
                            del module_config[key]

        # Clean up global config sections
        global_config = data.get("config", {})
        for section_name, section_data in global_config.items():
            if not isinstance(section_data, dict):
                continue
            schema = GLOBAL_CONFIG_SCHEMAS.get(section_name)
            if not schema or not schema.get("fields"):
                continue
            allowed_keys = set(schema["fields"].keys())
            for key in list(section_data.keys()):
                if key not in allowed_keys:
                    removed.append({
                        "location": f"config.{section_name}",
                        "key": key,
                        "value": section_data[key],
                    })
                    if not dry_run:
                        del section_data[key]

        if not dry_run and removed:
            _save_config(data)
            logger.info("Config cleanup: removed %d key(s)", len(removed))

        # Sanitise values for JSON response (avoid huge blobs)
        for entry in removed:
            v = entry["value"]
            if isinstance(v, (dict, list)):
                entry["value"] = f"({type(v).__name__}, {len(v)} items)"
            else:
                entry["value"] = str(v)[:120]

        return JSONResponse({
            "status": "ok",
            "dry_run": dry_run,
            "removed_count": len(removed),
            "removed": removed,
        })

    # ── Extension helpers ────────────────────────────────────────────

    def _get_extension_module_paths() -> list[str]:
        """Get the module paths (e.g. 'extensions.tricount') for all loaded extensions."""
        paths = []
        if bot and hasattr(bot, "ext"):
            for class_name, ext_instance in bot.ext.items():
                # interactions.py Extension stores the module name in extension_name
                module_path = getattr(ext_instance, "extension_name", None)
                if module_path:
                    paths.append(module_path)
                else:
                    # Fallback: derive from the class's module
                    mod = type(ext_instance).__module__
                    if mod:
                        paths.append(mod)
        return paths

    # ── Extension reload ─────────────────────────────────────────────

    @app.post("/api/reload")
    async def api_reload_all(request: Request):
        """Reload all extensions to apply config changes."""
        _require_admin(request)
        if not bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")

        results = {"reloaded": [], "failed": []}
        ext_paths = _get_extension_module_paths()
        for ext_path in ext_paths:
            try:
                bot.reload_extension(ext_path)
                results["reloaded"].append(ext_path)
                logger.info(f"Reloaded extension: {ext_path}")
            except Exception as e:
                results["failed"].append({"name": ext_path, "error": str(e)})
                logger.error(f"Failed to reload {ext_path}: {e}")
        return JSONResponse(results)

    @app.post("/api/reload/{ext_name:path}")
    async def api_reload_one(request: Request, ext_name: str):
        """Reload a single extension by module path (e.g. 'extensions.tricount')."""
        _require_admin(request)
        if not bot:
            raise HTTPException(status_code=503, detail="Bot non disponible")
        try:
            bot.reload_extension(ext_name)
            logger.info(f"Reloaded extension: {ext_name}")
            return JSONResponse({"status": "ok", "extension": ext_name})
        except Exception as e:
            logger.error(f"Failed to reload {ext_name}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Bot info ─────────────────────────────────────────────────────

    @app.get("/api/bot-info")
    async def api_bot_info(request: Request):
        """Get bot status information (available to all authenticated users, admin gets extra details)."""
        session = _require_session(request)
        is_admin = oauth.is_admin(session)
        info = {"status": "unknown", "guilds": 0}
        if bot:
            try:
                info["status"] = "online" if bot.is_ready else "starting"
                info["guilds"] = len(bot.guilds) if bot.guilds else 0
                info["user"] = str(bot.user) if bot.user else None
                info["latency"] = round(bot.latency * 1000) if bot.latency else None
                if is_admin:
                    info["extensions"] = _get_extension_module_paths()
                    info["module_extension_map"] = _build_module_to_extension_map()
                    info["bot_guild_ids"] = [str(g.id) for g in bot.guilds] if bot.guilds else []
            except Exception:
                info["status"] = "error"
        return JSONResponse(info)

    # ── Logs API ─────────────────────────────────────────────────────

    @app.get("/api/logs")
    async def api_get_logs(request: Request, count: int = 200, level: str = "",
                           search: str = "", logger_name: str = ""):
        """Get recent log entries with optional filtering."""
        _require_admin(request)
        handler = WebUILogHandler.get_instance()
        if not handler:
            return JSONResponse({"logs": []})
        logs = handler.get_recent(
            count=min(count, 2000),
            level=level or None,
            search=search or None,
            logger_name=logger_name or None,
        )
        return JSONResponse({"logs": logs})

    @app.get("/api/logs/stream")
    async def api_stream_logs(request: Request):
        """SSE endpoint for real-time log streaming."""
        _require_admin(request)
        handler = WebUILogHandler.get_instance()
        if not handler:
            raise HTTPException(status_code=503, detail="Log handler non disponible")

        queue = handler.subscribe()

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield {
                            "event": "log",
                            "data": json.dumps(entry.to_dict()),
                        }
                    except asyncio.TimeoutError:
                        # Send keepalive
                        yield {"event": "ping", "data": ""}
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                handler.unsubscribe(queue)

        return EventSourceResponse(event_generator())

    # ── Frontend serving ─────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        """Serve the main dashboard page."""
        return _serve_frontend()

    @app.get("/{path:path}", response_class=HTMLResponse)
    async def catch_all(request: Request, path: str):
        """Serve static files or fallback to frontend."""
        # Serve static assets
        static_path = os.path.join("src", "webui", "static", path)
        if os.path.isfile(static_path):
            import mimetypes
            content_type, _ = mimetypes.guess_type(static_path)
            with open(static_path, "r", encoding="utf-8") as f:
                content = f.read()
            return Response(content=content, media_type=content_type or "text/plain")
        return _serve_frontend()

    def _serve_frontend() -> HTMLResponse:
        """Load and return the frontend HTML."""
        frontend_path = os.path.join("src", "webui", "static", "index.html")
        try:
            with open(frontend_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        except FileNotFoundError:
            return HTMLResponse(
                content="<h1>Dashboard en construction</h1><p>Le fichier frontend n'a pas été trouvé.</p>",
                status_code=500,
            )

    return app
