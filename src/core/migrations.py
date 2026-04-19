"""One-time config.json migrations applied at bot startup.

These run before :func:`src.core.config.load_config` so extensions see the
post-migration shape. Migrations must be idempotent and cheap — they're
checked on every boot.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.core import logging as logutil
from src.core.config import CONFIG_PATH, config_store

logger = logutil.init_logger(os.path.basename(__file__))


CONFIG_MODULE_RENAMES: dict[str, str] = {
    "moduleColoc": "moduleZunivers",
}


def migrate_config_module_keys() -> bool:
    """Rename legacy per-server module keys in config.json.

    For every ``(old, new)`` pair in :data:`CONFIG_MODULE_RENAMES`, walk each
    ``servers.<guild_id>`` block and move ``old`` -> ``new`` when only ``old``
    exists. If both exist, drop ``old`` (assume the new key was hand-edited
    and is authoritative).

    Returns ``True`` when the file was rewritten.
    """
    path = Path(CONFIG_PATH)
    if not path.exists():
        return False

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("migrations: cannot parse %s: %s", CONFIG_PATH, e)
        return False

    servers = data.get("servers", {})
    if not isinstance(servers, dict):
        return False

    changed = False
    for guild_id, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        for old, new in CONFIG_MODULE_RENAMES.items():
            if old not in server_cfg:
                continue
            if new in server_cfg:
                server_cfg.pop(old)
                logger.warning(
                    "migrations: guild %s has both %s and %s; dropped %s",
                    guild_id,
                    old,
                    new,
                    old,
                )
            else:
                server_cfg[new] = server_cfg.pop(old)
                logger.info(
                    "migrations: guild %s renamed config key %s -> %s",
                    guild_id,
                    old,
                    new,
                )
            changed = True

    if changed:
        config_store.save_full(data)
        logger.info("migrations: config.json rewritten with module-key renames")
    return changed


__all__ = ["CONFIG_MODULE_RENAMES", "migrate_config_module_keys"]
