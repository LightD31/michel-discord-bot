"""FastAPI app factory.

Thin glue: builds the :class:`~src.webui.context.WebUIContext` and mounts the
domain-specific routers from ``src/webui/routes/`` and ``src/webui/sse/``.

The frontend catch-all router must be mounted **last** so API routes match
first.
"""

from fastapi import FastAPI

from src import logutil
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
    servers as servers_routes,
)
from src.webui.sse import logs as logs_sse

logger = logutil.init_logger("webui.app")


def create_app(bot=None, bot_loop=None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        bot: The ``interactions.py`` Client instance (optional, for live data).
        bot_loop: The event loop the bot runs on — required to invoke bot
            coroutines from the WebUI's thread.
    """
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

    app = FastAPI(title="Michel Bot Dashboard", docs_url=None, redoc_url=None)

    ctx = WebUIContext(bot=bot, bot_loop=bot_loop, oauth=oauth)

    # Order: API routers first, frontend catch-all last.
    app.include_router(auth_routes.create_router(ctx))
    app.include_router(config_routes.create_router(ctx))
    app.include_router(servers_routes.create_router(ctx))
    app.include_router(extensions_routes.create_router(ctx))
    app.include_router(bot_routes.create_router(ctx))
    app.include_router(logs_sse.create_router(ctx))
    app.include_router(frontend_routes.create_router(ctx))

    return app
