"""One-time config migrations, run by ``main.py`` before anything reads config.

Each migration is idempotent: it inspects the config and changes nothing once
the old shape is gone, so it can run at every startup. Delete a migration once
deployed configs are converted.
"""

from typing import Any

from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import ConfigError

logger = logutil.init_logger("migrations")

# The guild-links module (March 2026) became the Embed manager, but configs
# kept the old key. Its fields map onto the new module; the links themselves
# lived in code and are re-created as embeds from the dashboard.
_GUILDEUX_KEY = "moduleGuildeux"
_EMBED_MANAGER_KEY = "moduleEmbedManager"
_GUILDEUX_FIELDS = {"lienChannelId": "channelId", "lienMessageId": "messageId"}


def migrate_guildeux(data: dict[str, Any]) -> list[str]:
    """Fold ``moduleGuildeux`` into ``moduleEmbedManager``, in place.

    A guild with no Embed manager config gets one carrying the old channel and
    message (so the first publish edits the old links message in place) and
    the old enabled flag. A guild that already has one keeps it untouched.
    Either way the old key is removed. Returns the migrated guild ids.
    """
    migrated: list[str] = []
    for guild_id, server in (data.get("servers") or {}).items():
        if not isinstance(server, dict) or _GUILDEUX_KEY not in server:
            continue
        old = server.pop(_GUILDEUX_KEY)
        if _EMBED_MANAGER_KEY not in server and isinstance(old, dict):
            new: dict[str, Any] = {"enabled": bool(old.get("enabled", False)), "embeds": []}
            for old_field, new_field in _GUILDEUX_FIELDS.items():
                if old.get(old_field):
                    new[new_field] = old[old_field]
            server[_EMBED_MANAGER_KEY] = new
        migrated.append(str(guild_id))
    return migrated


def run_migrations() -> None:
    """Apply every pending migration; write the config only if one applied."""
    data = config_store.get()
    servers = data.get("servers") or {}
    if not any(isinstance(s, dict) and _GUILDEUX_KEY in s for s in servers.values()):
        return

    migrated: list[str] = []

    def mutator(fresh: dict[str, Any]) -> None:
        migrated.extend(migrate_guildeux(fresh))

    try:
        config_store.mutate(mutator)
    except ConfigError as e:
        logger.error("Config migration skipped: %s", e)
        return
    logger.info("Migrated %s to %s for guilds %s", _GUILDEUX_KEY, _EMBED_MANAGER_KEY, migrated)
