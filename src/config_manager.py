"""Configuration manager for loading and saving config/config.json."""

import json
import os
from typing import Tuple

from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

CONFIG_PATH = os.path.join("config", "config.json")


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
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)
    for server_id, server_info in data["servers"].items():
        if str(server_id) in enabled_servers:
            server_info[module_name] = module_config
        else:
            server_info[module_name] = {"enabled": False}
    data["config"] = config
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    logger.info(
        "Saved config for module %s for servers %s",
        module_name,
        enabled_servers,
    )


def load_discord2name(guild_id: str | int) -> dict:
    """Load the discord2name mapping for a specific guild."""
    data = load_full_config()
    return data.get("servers", {}).get(str(guild_id), {}).get("discord2name", {})
