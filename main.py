"""
Main script to run

This script initializes extensions and starts the bot
"""

import os
import sys

import interactions
from interactions import IntervalTrigger, Task

from config import DEBUG
from src import logutil
from src.config_manager import load_config

config, _, _ = load_config()

DEV_GUILD = config["discord"]["devGuildId"]
TOKEN = config["discord"]["botToken"]

# Web UI configuration
WEBUI_ENABLED = config.get("webui", {}).get("enabled", False)
WEBUI_HOST = config.get("webui", {}).get("host", "0.0.0.0")
WEBUI_PORT = config.get("webui", {}).get("port", 8080)

# Configure logging for this main.py handler
logger = logutil.init_logger("main.py")
logger.debug(
    "Debug mode is %s; This is not a warning, \
just an indicator. You may safely ignore",
    DEBUG,
)

if not TOKEN:
    logger.critical("TOKEN variable not set. Cannot continue")
    sys.exit(1)

client = interactions.Client(
    token=TOKEN,
    intents=interactions.Intents.ALL,
    send_not_ready_messages=True,
    delete_unused_application_cmds=True,
    # auto_defer=interactions.AutoDefer(enabled=True, time_until_defer=0),
    send_command_tracebacks=False,
)


HEALTH_FILE = "/tmp/bot_heartbeat"


@Task.create(IntervalTrigger(seconds=45))
async def _heartbeat_task():
    """Touch a file so the Docker healthcheck can verify the bot is alive (metadata-only, no disk write)."""
    from pathlib import Path

    try:
        Path(HEALTH_FILE).touch()
    except OSError:
        pass


@interactions.listen()
async def on_startup():
    """Called when the bot starts"""
    logger.info(f"Logged in as {client.user}")
    _heartbeat_task.start()

    # Start Web UI if enabled
    if WEBUI_ENABLED:
        try:
            from src.webui.server import start_webui

            start_webui(bot=client, host=WEBUI_HOST, port=WEBUI_PORT)
        except Exception as e:
            logger.error(f"Failed to start Web UI: {e}")


# get all python files in "extensions" folder
# Extension enabled state is controlled via config["extensions"][ext_path] (bool).
# Default: non-underscore-prefixed extensions are enabled, underscore-prefixed are disabled.
extension_config = config.get("extensions", {})
extensions = [
    f"extensions.{f[:-3]}"
    for f in os.listdir("extensions")
    if f.endswith(".py") and not f.startswith("__")
]
for extension in extensions:
    short_name = extension[len("extensions.") :]
    default_enabled = not short_name.startswith("_")
    if not extension_config.get(extension, default_enabled):
        logger.debug(f"Skipping disabled extension {extension}")
        continue
    try:
        client.load_extension(extension)
        logger.info(f"Loaded extension {extension}")
    except interactions.errors.ExtensionLoadException as e:
        logger.exception(f"Failed to load extension {extension}.", exc_info=e)
client.start()
