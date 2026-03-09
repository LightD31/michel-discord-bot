"""Configuration manager for loading config/config.json."""

import json
import os

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


def load_config(module_name: str | None = None) -> tuple[dict, dict, list[str]]:
    """Load the configuration for a specific module."""
    data = load_full_config()
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
