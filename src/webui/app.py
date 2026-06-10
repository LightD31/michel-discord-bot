"""FastAPI app factory.

Thin glue: builds the :class:`~src.webui.context.WebUIContext` and mounts the
domain-specific routers from ``src/webui/routes/`` and ``src/webui/sse/``.

The frontend catch-all router must be mounted **last** so API routes match
first.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.core import logging as logutil
from src.core.config import config_write_error
from src.core.config import load_config as bot_load_config
from src.webui.auth import DiscordOAuth
from src.webui.context import WebUIContext
from src.webui.log_handler import WebUILogHandler, install_log_handler
from src.webui.routes import (
    auth as auth_routes,
)
from src.webui.routes import (
    bot as bot_routes,
)
from src.webui.routes import (
    config as config_routes,
)
from src.webui.routes import (
    extensions as extensions_routes,
)
from src.webui.routes import (
    frontend as frontend_routes,
)
from src.webui.routes import (
    moderation as moderation_routes,
)
from src.webui.routes import (
    rolemenus as rolemenus_routes,
)
from src.webui.routes import (
    servers as servers_routes,
)
from src.webui.routes import (
    spotify as spotify_routes,
)
from src.webui.sse import logs as logs_sse

logger = logutil.init_logger("webui.app")

# How often the background task evicts expired sessions (memory + MongoDB).
_SESSION_CLEANUP_INTERVAL_SECONDS = 1800.0


async def _session_cleanup_loop(oauth: DiscordOAuth) -> None:
    while True:
        await asyncio.sleep(_SESSION_CLEANUP_INTERVAL_SECONDS)
        try:
            await oauth.purge_expired()
        except Exception as e:
            logger.warning("Session cleanup tick failed: %s", e)


def create_app(bot=None, bot_loop=None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        bot: The ``interactions.py`` Client instance (optional, for live data).
        bot_loop: The event loop the bot runs on — required to invoke bot
            coroutines from the WebUI's thread.
    """
    write_err = config_write_error()
    if write_err:
        logger.error(
            "Le dossier config/ n'est pas inscriptible (%s) — toutes les sauvegardes du "
            "dashboard échoueront. Sur l'hôte : chown -R 1000:1000 ./config",
            write_err,
        )

    config, _, _ = bot_load_config()
    webui_config = config.get("webui", {})
    discord_config = config.get("discord", {})

    client_id = webui_config.get("clientId") or discord_config.get("clientId", "")
    client_secret = webui_config.get("clientSecret", "")
    base_url = webui_config.get("baseUrl", "http://localhost:8080")
    redirect_uri = f"{base_url}/auth/callback"
    developer_user_ids = webui_config.get("developerUserIds", [])

    oauth = DiscordOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        developer_user_ids=developer_user_ids,
    )

    # Install WebUI log handler on first app creation.
    if not WebUILogHandler.get_instance():
        install_log_handler(max_entries=2000)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Runs on uvicorn's own loop: restore persisted sessions so a bot
        # restart doesn't log every admin out, then keep them pruned.
        try:
            from src.webui.sessions import SessionRepository

            await SessionRepository().ensure_indexes()
        except Exception as e:
            logger.warning("Web UI session TTL index setup failed: %s", e)
        await oauth.restore_sessions()
        cleanup_task = asyncio.create_task(_session_cleanup_loop(oauth))
        try:
            yield
        finally:
            cleanup_task.cancel()

    app = FastAPI(title="Michel Bot Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)

    ctx = WebUIContext(bot=bot, bot_loop=bot_loop, oauth=oauth)

    # Order: API routers first, frontend catch-all last.
    app.include_router(auth_routes.create_router(ctx))
    app.include_router(config_routes.create_router(ctx))
    app.include_router(servers_routes.create_router(ctx))
    app.include_router(rolemenus_routes.create_router(ctx))
    app.include_router(moderation_routes.create_router(ctx))
    app.include_router(extensions_routes.create_router(ctx))
    app.include_router(spotify_routes.create_router(ctx))
    app.include_router(bot_routes.create_router(ctx))
    app.include_router(logs_sse.create_router(ctx))
    app.include_router(frontend_routes.create_router(ctx))

    return app
