"""Configuration manager for loading and saving config/config.json.

Writes are performed atomically: JSON is serialized to a temp file in the same
directory and then moved into place with ``os.replace``. A module-level lock
serializes concurrent writers (the bot's asyncio loop and the WebUI uvicorn
thread both call into this module).
"""

import json
import os
import tempfile
import threading
from typing import Tuple

from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

CONFIG_PATH = os.path.join("config", "config.json")

# Serializes concurrent writers. The bot runs an asyncio loop and the WebUI
# runs in a separate daemon thread (uvicorn), so a threading.Lock is the right
# primitive here — asyncio.Lock would not protect cross-thread access.
_write_lock = threading.Lock()


def _load_config_file() -> dict:
    """Load config/config.json."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        logger.error("Configuration file not found: %s", CONFIG_PATH)
        return {}
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s", CONFIG_PATH, e)
        return {}


def _atomic_write(data: dict) -> None:
    """Serialize *data* to CONFIG_PATH atomically.

    Writes to a tempfile in the same directory (so ``os.replace`` stays on the
    same filesystem and is atomic) and then moves it over the target. A crash
    mid-write leaves the previous config intact.
    """
    directory = os.path.dirname(CONFIG_PATH) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=".config-", suffix=".json.tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=4, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        # Best-effort cleanup of the temp file if the move didn't happen.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_full_config() -> dict:
    """Load the complete configuration."""
    return _load_config_file()


def load_config(module_name: str | None = None) -> Tuple[dict, dict, list[str]]:
    """Load the configuration for a specific module.

    Returns:
        A tuple of (global_config, per_server_module_config, enabled_server_ids).
    """
    data = load_full_config()
    if not data:
        return {}, {}, []

    config = data.get("config", {})

    if module_name is None:
        return config, {}, []

    servers = data.get("servers", {})
    enabled_servers = [
        str(server_id)
        for server_id, server_info in servers.items()
        if server_info.get(module_name, {}).get("enabled", False)
    ]

    module_config = {
        server_id: server_info.get(module_name, {})
        for server_id, server_info in servers.items()
        if str(server_id) in enabled_servers
    }

    logger.info(
        "Loaded config for module %s for servers %s",
        module_name,
        enabled_servers,
    )

    return config, module_config, enabled_servers


def save_config(
    module_name: str, config: dict, module_config: dict, enabled_servers: list[str]
) -> None:
    """Save the configuration for a specific module."""
    with _write_lock:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            data = json.load(file)
        for server_id, server_info in data["servers"].items():
            if str(server_id) in enabled_servers:
                server_info[module_name] = module_config
            else:
                server_info[module_name] = {"enabled": False}
        data["config"] = config
        _atomic_write(data)
    logger.info(
        "Saved config for module %s for servers %s",
        module_name,
        enabled_servers,
    )


def load_discord2name(guild_id: str | int) -> dict:
    """Load the discord2name mapping for a specific guild."""
    data = load_full_config()
    return data.get("servers", {}).get(str(guild_id), {}).get("discord2name", {})


def save_module_field(
    module_name: str, guild_id: str | int, field: str, value
) -> None:
    """Update a single field inside servers.<guild_id>.<module_name>.

    Creates intermediate dicts if missing.
    """
    with _write_lock:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            data = {}
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in %s: %s", CONFIG_PATH, e)
            return

        servers = data.setdefault("servers", {})
        guild = servers.setdefault(str(guild_id), {})
        module = guild.setdefault(module_name, {})
        module[field] = value

        _atomic_write(data)
    logger.info(
        "Updated config: servers.%s.%s.%s", guild_id, module_name, field
    )
