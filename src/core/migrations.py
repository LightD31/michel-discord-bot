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


# The AI model list (October 2026) moved out of the OpenRouter section into a
# provider-neutral ``AI`` section once NanoGPT became a second API; each model
# now names the API that serves it, and the pre-existing ones were OpenRouter's.
_AI_SECTION = "AI"
_OPENROUTER_SECTION = "OpenRouter"
_AI_MOVED_KEYS = ("models", "modelsToCompare")


def _needs_ai_section(data: dict[str, Any]) -> bool:
    openrouter = (data.get("config") or {}).get(_OPENROUTER_SECTION)
    return isinstance(openrouter, dict) and any(k in openrouter for k in _AI_MOVED_KEYS)


def migrate_ai_section(data: dict[str, Any]) -> list[str]:
    """Move ``OpenRouter.models`` / ``modelsToCompare`` into ``AI``, in place.

    Values already present in ``AI`` win (the old keys are dropped either
    way), and moved models without an ``api`` are tagged ``openrouter``.
    Returns the moved keys.
    """
    config = data.get("config")
    if not isinstance(config, dict):
        return []
    openrouter = config.get(_OPENROUTER_SECTION)
    if not isinstance(openrouter, dict):
        return []
    ai = config.get(_AI_SECTION)
    if not isinstance(ai, dict):
        ai = {}
    moved: list[str] = []
    for key in _AI_MOVED_KEYS:
        if key not in openrouter:
            continue
        value = openrouter.pop(key)
        moved.append(key)
        if key in ai:
            continue
        if key == "models" and isinstance(value, list):
            value = [
                {**entry, "api": entry.get("api") or "openrouter"}
                if isinstance(entry, dict)
                else entry
                for entry in value
            ]
        ai[key] = value
    if moved:
        config[_AI_SECTION] = ai
    return moved


def _needs_guildeux(data: dict[str, Any]) -> bool:
    servers = data.get("servers") or {}
    return any(isinstance(s, dict) and _GUILDEUX_KEY in s for s in servers.values())


def run_migrations() -> None:
    """Apply every pending migration; write the config only if one applied."""
    data = config_store.get()
    if not (_needs_guildeux(data) or _needs_ai_section(data)):
        return

    guilds: list[str] = []
    ai_keys: list[str] = []

    def mutator(fresh: dict[str, Any]) -> None:
        guilds.extend(migrate_guildeux(fresh))
        ai_keys.extend(migrate_ai_section(fresh))

    try:
        config_store.mutate(mutator)
    except ConfigError as e:
        logger.error("Config migration skipped: %s", e)
        return
    if guilds:
        logger.info("Migrated %s to %s for guilds %s", _GUILDEUX_KEY, _EMBED_MANAGER_KEY, guilds)
    if ai_keys:
        logger.info("Moved %s from %s to %s", ai_keys, _OPENROUTER_SECTION, _AI_SECTION)
