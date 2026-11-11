"""
Main script to run

This script initializes extensions and starts the bot
"""

import asyncio
import contextlib
import logging
import os
import sys
import threading
import time
from pathlib import Path

import interactions
from interactions import IntervalTrigger, Task

from src.core import error_reporting
from src.core import logging as logutil
from src.core.config import CONFIG_PATH, bind_host, global_config
from src.core.db import mongo_manager
from src.core.http import http_client
from src.core.logging import DEBUG
from src.discord_ext import task_health
from src.discord_ext.interaction_context import bind_interaction_context
from src.discord_ext.lifecycle import GracefulShutdown

# Configure logging for this main.py handler
logger = logutil.init_logger("main.py")
# interactions.py's own errors (a failing Task…) in our format and the dashboard.
logutil.adopt_library_logger("interactions")
# Every Task run is timed, recorded for /metrics and tagged in the logs.
task_health.install()

config = global_config()
if not config.get("discord", {}).get("botToken"):
    # The store has already logged why the file couldn't be read, if it couldn't.
    logger.critical(
        "No bot token in %s (discord.botToken). Copy config.example.json to %s, "
        "fill in the token, and start again.",
        CONFIG_PATH,
        CONFIG_PATH,
    )
    sys.exit(1)

DEV_GUILD = config["discord"].get("devGuildId")
TOKEN = config["discord"]["botToken"]

if error_reporting.init(config.get("errorReporting") or {}):
    logger.info("Error reporting to Sentry/GlitchTip enabled")

# Web UI configuration
WEBUI_ENABLED = config.get("webui", {}).get("enabled", False)
WEBUI_HOST = bind_host(config.get("webui", {}).get("host"))
WEBUI_PORT = config.get("webui", {}).get("port", 8080)

logger.debug(
    "Debug mode is %s; This is not a warning, \
just an indicator. You may safely ignore",
    DEBUG,
)

# Only the intents the extensions actually consume — slash commands and
# components need none of these. Privileged intents (Developer Portal
# toggles): GUILD_MEMBERS (welcome, userinfo) and MESSAGE_CONTENT (feur,
# automod filters). GUILD_PRESENCES is deliberately not requested. Add the
# matching intent here when a new extension listens to a new gateway event.
INTENTS = (
    interactions.Intents.GUILDS  # guild/channel cache — required baseline
    | interactions.Intents.GUILD_MEMBERS  # welcome + userinfo member events, lldap role sync
    | interactions.Intents.GUILD_MESSAGES  # feur, xp leveling, automod, AI mention replies
    | interactions.Intents.MESSAGE_CONTENT  # feur keyword match, automod filters, AI context
    | interactions.Intents.GUILD_MESSAGE_REACTIONS  # giveaway, polls, starboard
    | interactions.Intents.GUILD_VOICE_STATES  # xp voice activity
    | interactions.Intents.GUILD_SCHEDULED_EVENTS  # minecraft, twitch, zevent scheduled events
)

client = interactions.Client(
    token=TOKEN,
    intents=INTENTS,
    send_not_ready_messages=True,
    delete_unused_application_cmds=True,
    # auto_defer=interactions.AutoDefer(enabled=True, time_until_defer=0),
    send_command_tracebacks=False,
    # Tags what each command, button and modal logs with its guild, its name and
    # a correlation id (the reference a member sees if it fails).
    global_pre_run_callback=bind_interaction_context,
)


# SIGTERM (docker compose stop) and SIGINT stop the bot cleanly instead of
# killing it mid-task; installed on the bot loop once it runs.
shutdown = GracefulShutdown(
    client, closers=[http_client.close, mongo_manager.close, error_reporting.flush]
)


HEALTH_FILE = "/tmp/bot_heartbeat"
# If the heartbeat is older than this, the bot loop is wedged or dead — exit so
# the supervisor (Docker `restart: unless-stopped`) restarts the process.
WATCHDOG_STALE_SECONDS = 180
WATCHDOG_GRACE_SECONDS = 120


@Task.create(IntervalTrigger(seconds=45))
async def _heartbeat_task():
    """Touch a file so the Docker healthcheck can verify the bot is alive (metadata-only, no disk write)."""
    with contextlib.suppress(OSError):
        Path(HEALTH_FILE).touch()


def _watchdog_loop():
    """Exit the process if the heartbeat goes stale.

    Runs in a daemon thread: it only needs to outlive a wedged event loop, and
    the main thread stays parked in ``client.start()`` for as long as the
    process is alive. Combined with Docker's restart policy, this gives us
    automatic recovery from wedged gateway connections.
    """
    started = time.time()
    while True:
        time.sleep(30)
        # During the grace window the loop may not have produced a heartbeat yet.
        if time.time() - started < WATCHDOG_GRACE_SECONDS:
            continue
        try:
            mtime = Path(HEALTH_FILE).stat().st_mtime
        except FileNotFoundError:
            mtime = 0
        except OSError:
            continue
        age = time.time() - mtime
        if age > WATCHDOG_STALE_SECONDS:
            logger.critical(
                "Heartbeat stale (%.0fs > %ds) — exiting so the supervisor restarts the bot",
                age,
                WATCHDOG_STALE_SECONDS,
            )
            os._exit(1)


@interactions.listen()
async def on_startup():
    """Called when the bot starts"""
    logger.info(f"Logged in as {client.user}")
    _heartbeat_task.start()
    shutdown.install(asyncio.get_running_loop())

    # Start Web UI if enabled
    if WEBUI_ENABLED:
        try:
            from src.webui.server import start_webui

            start_webui(bot=client, host=WEBUI_HOST, port=WEBUI_PORT)
        except Exception as e:
            logger.error(f"Failed to start Web UI: {e}")


# Discover extensions: either single-file modules (extensions/<name>.py) or
# packages (extensions/<name>/__init__.py). Enabled state is controlled via
# config["extensions"][ext_path] (bool). Default: non-underscore-prefixed
# entries are enabled, underscore-prefixed are disabled.
extension_config = config.get("extensions", {})
extensions = []
for entry in os.listdir("extensions"):
    if entry.startswith("__"):
        continue
    full_path = os.path.join("extensions", entry)
    if entry.endswith(".py") and os.path.isfile(full_path):
        extensions.append(f"extensions.{entry[:-3]}")
    elif os.path.isdir(full_path) and os.path.isfile(os.path.join(full_path, "__init__.py")):
        extensions.append(f"extensions.{entry}")
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

threading.Thread(target=_watchdog_loop, name="bot-watchdog", daemon=True).start()
try:
    client.start()
except Exception:
    logger.exception("client.start() exited with an exception — restarting via supervisor")
    os._exit(1)
else:
    if shutdown.requested:
        logger.info("Bot stopped cleanly")
        logging.shutdown()
        os._exit(0)
    # Reaching here otherwise means the gateway connection ended without
    # raising. The event loop is closed; let the supervisor restart us instead
    # of leaving a half-dead process with a live Web UI thread.
    logger.warning("client.start() returned — exiting so the supervisor restarts the bot")
    os._exit(1)
