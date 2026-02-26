"""
Main script to run

This script initializes extensions and starts the bot
"""

import os
import sys

import interactions

from config import DEBUG
from src import logutil
from src.utils import load_config

config,_,_ = load_config()

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


@interactions.listen()
async def on_startup():
    """Called when the bot starts"""
    logger.info(f"Logged in as {client.user}")

    # Start Web UI if enabled
    if WEBUI_ENABLED:
        try:
            from src.webui.server import start_webui
            start_webui(bot=client, host=WEBUI_HOST, port=WEBUI_PORT)
        except Exception as e:
            logger.error(f"Failed to start Web UI: {e}")


# get all python files in "extensions" folder
extensions = [
    f"extensions.{f[:-3]}"
    for f in os.listdir("extensions")
    if f.endswith(".py") and not f.startswith("_")
]
for extension in extensions:
    try:
        client.load_extension(extension)
        logger.info(f"Loaded extension {extension}")
    except interactions.errors.ExtensionLoadException as e:
        logger.exception(f"Failed to load extension {extension}.", exc_info=e)
client.start()
