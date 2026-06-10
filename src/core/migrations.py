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

# Weighted message lists migrated from parallel arrays (list of strings +
# sibling list of numbers) to a single list of {"text", "weight"} objects.
WEIGHTED_MESSAGE_LISTS: dict[str, list[tuple[str, str]]] = {
    "moduleWelcome": [
        ("welcomeMessageList", "welcomeMessageWeights"),
        ("leaveMessageList", "leaveMessageWeights"),
    ],
    "moduleBirthday": [("birthdayMessageList", "birthdayMessageWeights")],
    "moduleXp": [("levelUpMessageList", "levelUpMessageWeights")],
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


def _merge_weighted_message_lists(data: dict) -> bool:
    """Merge parallel message/weight arrays into ``[{"text", "weight"}]`` in place.

    For every ``(list_key, weights_key)`` of :data:`WEIGHTED_MESSAGE_LISTS`:
    string entries are zipped with their weight (missing/invalid weights
    default to 1, surplus weights are dropped), already-converted dict entries
    are kept as-is, and the now-redundant weights key is removed.

    Returns ``True`` when *data* was modified.
    """
    servers = data.get("servers", {})
    if not isinstance(servers, dict):
        return False

    changed = False
    for guild_id, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        for module_name, pairs in WEIGHTED_MESSAGE_LISTS.items():
            module_cfg = server_cfg.get(module_name)
            if not isinstance(module_cfg, dict):
                continue
            for list_key, weights_key in pairs:
                weights = module_cfg.get(weights_key)
                if not isinstance(weights, list):
                    weights = []
                messages = module_cfg.get(list_key)
                if isinstance(messages, list) and any(isinstance(m, str) for m in messages):
                    merged: list = []
                    for i, message in enumerate(messages):
                        if isinstance(message, str):
                            weight = weights[i] if i < len(weights) else 1
                            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                                weight = 1
                            merged.append({"text": message, "weight": weight})
                        else:
                            merged.append(message)
                    module_cfg[list_key] = merged
                    changed = True
                    logger.info(
                        "migrations: guild %s merged %s + %s into objects",
                        guild_id,
                        list_key,
                        weights_key,
                    )
                if weights_key in module_cfg:
                    module_cfg.pop(weights_key)
                    changed = True
    return changed


def migrate_weighted_message_lists() -> bool:
    """Apply :func:`_merge_weighted_message_lists` to config.json on disk.

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

    if not _merge_weighted_message_lists(data):
        return False

    config_store.save_full(data)
    logger.info("migrations: config.json rewritten with merged weighted message lists")
    return True


__all__ = [
    "CONFIG_MODULE_RENAMES",
    "WEIGHTED_MESSAGE_LISTS",
    "migrate_config_module_keys",
    "migrate_weighted_message_lists",
]
