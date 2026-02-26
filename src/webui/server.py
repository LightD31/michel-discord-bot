"""
Web UI server runner.
Can be started alongside the bot or independently.
"""

import threading
import uvicorn

from src import logutil
from src.webui.app import create_app

logger = logutil.init_logger("webui.server")


def start_webui(bot=None, host: str = "0.0.0.0", port: int = 8080):
    """
    Start the web UI server in a background thread.
    
    Args:
        bot: The interactions.py Client instance (optional).
        host: Host to bind to.
        port: Port to listen on.
    """
    app = create_app(bot=bot)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True, name="webui-server")
    thread.start()
    logger.info(f"Web UI started on http://{host}:{port}")
    return server, thread


def run_standalone():
    """Run the web UI as a standalone server (without the bot)."""
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    run_standalone()
