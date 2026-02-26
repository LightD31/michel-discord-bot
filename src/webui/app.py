"""
FastAPI application for the Web UI dashboard.
Provides config management endpoints and serves the frontend.
"""

import json
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from src import logutil
from src.utils import load_config as bot_load_config
from src.webui.auth import DiscordOAuth, Session

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

    app = FastAPI(title="Michel Bot Dashboard", docs_url=None, redoc_url=None)

    # ── Helpers ──────────────────────────────────────────────────────

    COOKIE_NAME = "michel_session"

    def _get_full_config() -> dict:
        """Load the full raw config from disk."""
        try:
            from src.config_manager import ConfigManager
            cm = ConfigManager()
            return cm.load_full_config()
        except Exception:
            try:
                with open("config/config.json", "r", encoding="utf-8") as f:
                    return json.load(f)
            except FileNotFoundError:
                return {"config": {}, "servers": {}}

    def _save_full_config(data: dict):
        """Save the full config back to disk."""
        os.makedirs("config", exist_ok=True)
        with open("config/config.json", "w", encoding="utf-8") as f:
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
                        # Look for load_config("moduleXxx") calls
                        import re
                        for match in re.finditer(r'load_config\(["\'](\w+)["\']\)', content):
                            modules.add(match.group(1))
                    except Exception:
                        pass
        return sorted(modules)

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
        return JSONResponse({
            "authenticated": True,
            "user_id": session.user_id,
            "username": session.username,
            "avatar": session.avatar,
            "guilds": [
                {
                    "id": g["id"],
                    "name": g["name"],
                    "icon": g.get("icon"),
                    "managed": True,
                }
                for g in oauth.get_user_managed_guilds(session)
            ],
        })

    # ── Config API routes ────────────────────────────────────────────

    @app.get("/api/config")
    async def api_get_config(request: Request):
        """Get the full configuration."""
        _require_session(request)
        data = _get_full_config()
        return JSONResponse(data)

    @app.get("/api/modules")
    async def api_get_modules(request: Request):
        """Get all discovered module names."""
        _require_session(request)
        return JSONResponse({"modules": _discover_modules()})

    @app.get("/api/servers")
    async def api_get_servers(request: Request):
        """Get all configured servers."""
        session = _require_session(request)
        data = _get_full_config()
        servers = data.get("servers", {})

        # Enrich with guild names from Discord if available
        user_guilds = {g["id"]: g for g in session.guilds}
        result = {}
        for server_id, server_config in servers.items():
            guild_info = user_guilds.get(str(server_id), {})
            result[server_id] = {
                "name": guild_info.get("name", f"Serveur {server_id}"),
                "icon": guild_info.get("icon"),
                "config": server_config,
            }
        return JSONResponse(result)

    @app.get("/api/servers/{server_id}")
    async def api_get_server(request: Request, server_id: str):
        """Get configuration for a specific server."""
        _require_session(request)
        data = _get_full_config()
        server_config = data.get("servers", {}).get(server_id)
        if server_config is None:
            raise HTTPException(status_code=404, detail="Serveur non trouvé")
        return JSONResponse({"server_id": server_id, "config": server_config})

    @app.put("/api/servers/{server_id}/modules/{module_name}")
    async def api_update_module(request: Request, server_id: str, module_name: str, body: ConfigUpdate):
        """Update a specific module's config for a server."""
        _require_session(request)
        data = _get_full_config()

        if server_id not in data.get("servers", {}):
            raise HTTPException(status_code=404, detail="Serveur non trouvé")

        data["servers"][server_id][module_name] = body.config
        _save_full_config(data)
        logger.info(f"Updated {module_name} config for server {server_id}")
        return JSONResponse({"status": "ok"})

    @app.post("/api/servers/{server_id}/modules/{module_name}/toggle")
    async def api_toggle_module(request: Request, server_id: str, module_name: str, body: ModuleToggle):
        """Enable or disable a module for a server."""
        _require_session(request)
        data = _get_full_config()

        if server_id not in data.get("servers", {}):
            raise HTTPException(status_code=404, detail="Serveur non trouvé")

        if module_name not in data["servers"][server_id]:
            data["servers"][server_id][module_name] = {}

        data["servers"][server_id][module_name]["enabled"] = body.enabled
        _save_full_config(data)
        logger.info(f"{'Enabled' if body.enabled else 'Disabled'} {module_name} for server {server_id}")
        return JSONResponse({"status": "ok", "enabled": body.enabled})

    @app.get("/api/global-config")
    async def api_get_global_config(request: Request):
        """Get global (non-server-specific) configuration."""
        _require_session(request)
        data = _get_full_config()
        return JSONResponse(data.get("config", {}))

    @app.put("/api/global-config/{section}")
    async def api_update_global_config(request: Request, section: str, body: GlobalConfigUpdate):
        """Update a section of the global configuration."""
        _require_session(request)
        data = _get_full_config()
        data.setdefault("config", {})[section] = body.config
        _save_full_config(data)
        logger.info(f"Updated global config section: {section}")
        return JSONResponse({"status": "ok"})

    # ── Bot info ─────────────────────────────────────────────────────

    @app.get("/api/bot-info")
    async def api_bot_info(request: Request):
        """Get bot status information."""
        _require_session(request)
        info = {"status": "unknown", "guilds": 0, "extensions": []}
        if bot:
            try:
                info["status"] = "online" if bot.is_ready else "starting"
                info["guilds"] = len(bot.guilds) if bot.guilds else 0
                info["user"] = str(bot.user) if bot.user else None
                info["extensions"] = list(bot.ext.keys()) if hasattr(bot, "ext") else []
                info["latency"] = round(bot.latency * 1000) if bot.latency else None
            except Exception:
                info["status"] = "error"
        return JSONResponse(info)

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
