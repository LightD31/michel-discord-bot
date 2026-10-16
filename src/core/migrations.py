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


# The ZUnivers Ninja URLs (October 2026) started as per-guild ``moduleZunivers``
# fields; Ninja is one server for the whole bot, so they moved to a global section.
_ZUNIVERS_KEY = "moduleZunivers"
_NINJA_SECTION = "zuniversNinja"
_NINJA_FIELDS = {"ninjaUrl": "apiUrl", "ninjaWebUrl": "webUrl"}


def _needs_ninja_section(data: dict[str, Any]) -> bool:
    servers = data.get("servers") or {}
    return any(
        isinstance(s, dict)
        and isinstance(s.get(_ZUNIVERS_KEY), dict)
        and any(k in s[_ZUNIVERS_KEY] for k in _NINJA_FIELDS)
        for s in servers.values()
    )


def migrate_ninja_section(data: dict[str, Any]) -> list[str]:
    """Move ``moduleZunivers.ninjaUrl`` / ``ninjaWebUrl`` into ``zuniversNinja``, in place.

    The first non-empty value found wins, and a value already set in the section
    is kept; the per-guild keys are dropped either way. Returns the guild ids that
    carried them.
    """
    migrated: list[str] = []
    found: dict[str, Any] = {}
    for guild_id, server in (data.get("servers") or {}).items():
        module = server.get(_ZUNIVERS_KEY) if isinstance(server, dict) else None
        if not isinstance(module, dict) or not any(k in module for k in _NINJA_FIELDS):
            continue
        for old_field, new_field in _NINJA_FIELDS.items():
            value = module.pop(old_field, None)
            if value and new_field not in found:
                found[new_field] = value
        migrated.append(str(guild_id))
    if found:
        config = data.setdefault("config", {})
        section = config.get(_NINJA_SECTION)
        if not isinstance(section, dict):
            section = {}
        for key, value in found.items():
            if not section.get(key):
                section[key] = value
        config[_NINJA_SECTION] = section
    return migrated


# The Uptime extension (October 2026) reads Kuma's /metrics with an API key
# instead of logging into its Socket.IO API, so the account credentials it
# used to keep are dropped rather than left lying in the config.
_UPTIME_SECTION = "uptimeKuma"
_UPTIME_OBSOLETE_KEYS = ("uptimeKumaUsername", "uptimeKumaPassword", "uptimeKuma2FA")


def _needs_uptime_credentials(data: dict[str, Any]) -> bool:
    section = (data.get("config") or {}).get(_UPTIME_SECTION)
    return isinstance(section, dict) and any(k in section for k in _UPTIME_OBSOLETE_KEYS)


def migrate_uptime_credentials(data: dict[str, Any]) -> list[str]:
    """Remove the Uptime Kuma username, password and 2FA code, in place. Returns the keys removed."""
    section = (data.get("config") or {}).get(_UPTIME_SECTION)
    if not isinstance(section, dict):
        return []
    removed = [key for key in _UPTIME_OBSOLETE_KEYS if key in section]
    for key in removed:
        del section[key]
    return removed


def _needs_guildeux(data: dict[str, Any]) -> bool:
    servers = data.get("servers") or {}
    return any(isinstance(s, dict) and _GUILDEUX_KEY in s for s in servers.values())


def run_migrations() -> None:
    """Apply every pending migration; write the config only if one applied."""
    data = config_store.get()
    if not (
        _needs_guildeux(data)
        or _needs_ai_section(data)
        or _needs_ninja_section(data)
        or _needs_uptime_credentials(data)
    ):
        return

    guilds: list[str] = []
    ai_keys: list[str] = []
    ninja_guilds: list[str] = []
    uptime_keys: list[str] = []

    def mutator(fresh: dict[str, Any]) -> None:
        guilds.extend(migrate_guildeux(fresh))
        ai_keys.extend(migrate_ai_section(fresh))
        ninja_guilds.extend(migrate_ninja_section(fresh))
        uptime_keys.extend(migrate_uptime_credentials(fresh))

    try:
        config_store.mutate(mutator)
    except ConfigError as e:
        logger.error("Config migration skipped: %s", e)
        return
    if guilds:
        logger.info("Migrated %s to %s for guilds %s", _GUILDEUX_KEY, _EMBED_MANAGER_KEY, guilds)
    if ai_keys:
        logger.info("Moved %s from %s to %s", ai_keys, _OPENROUTER_SECTION, _AI_SECTION)
    if ninja_guilds:
        logger.info(
            "Moved the ZUnivers Ninja URLs of guilds %s to the %s section",
            ninja_guilds,
            _NINJA_SECTION,
        )
    if uptime_keys:
        logger.info(
            "Removed the unused Uptime Kuma credentials %s (the bot reads /metrics with an API key)",
            uptime_keys,
        )
