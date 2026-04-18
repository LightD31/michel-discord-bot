"""Configuration manager for ``config/config.json``.

Moved from ``src/config_manager.py``. Adds a reactive :class:`ConfigStore` so
code can observe config changes instead of each extension holding its own stale
copy from import time.

Writes are atomic (tempfile + ``os.replace`` + ``fsync``) and serialized by a
``threading.Lock`` — the bot runs an asyncio loop and the optional WebUI runs
in a separate uvicorn thread, so a thread-aware primitive is required.
"""

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from src.core import logging as _logging

logger = _logging.init_logger(os.path.basename(__file__))

CONFIG_PATH = os.path.join("config", "config.json")


# ---------------------------------------------------------------------------
# Low-level filesystem helpers
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()


def _load_config_file() -> dict:
    """Read and parse ``config/config.json``. Returns ``{}`` on failure."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        logger.error("Configuration file not found: %s", CONFIG_PATH)
        return {}
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s", CONFIG_PATH, e)
        return {}


def _atomic_write(data: dict) -> None:
    """Serialize *data* to ``CONFIG_PATH`` atomically.

    Writes to a tempfile in the same directory (so ``os.replace`` stays on the
    same filesystem and remains atomic) then moves it over the target. A crash
    mid-write leaves the previous config intact.
    """
    directory = os.path.dirname(CONFIG_PATH) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".config-", suffix=".json.tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=4, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Reactive store — new in Phase 1
# ---------------------------------------------------------------------------


class ConfigStore:
    """Singleton-style reactive wrapper around ``config/config.json``.

    Purpose
    -------
    Extensions currently load the config dict at import time and never see
    edits the WebUI makes to disk. The store holds the authoritative in-memory
    copy and notifies subscribers on every successful save or reload so
    extensions can refresh their state without a bot restart.

    Usage::

        from src.core.config import config_store

        def on_config_change(new_config: dict) -> None:
            ...

        config_store.subscribe(on_config_change)
        data = config_store.get()            # cached, loaded on first call
        config_store.save_full(new_data)     # writes + notifies
        config_store.reload()                # re-reads from disk + notifies

    Subscribers are called synchronously in registration order. If a
    subscriber needs async work it can ``asyncio.create_task(...)`` itself.
    Exceptions inside a subscriber are logged and swallowed so a misbehaving
    callback can't block other listeners.
    """

    _instance: Optional["ConfigStore"] = None

    def __new__(cls) -> "ConfigStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = None  # type: ignore[attr-defined]
            cls._instance._subscribers = []  # type: ignore[attr-defined]
            cls._instance._state_lock = threading.Lock()  # type: ignore[attr-defined]
        return cls._instance

    # --- Read ---------------------------------------------------------

    def get(self) -> dict:
        """Return the cached config dict, loading it from disk on first call."""
        with self._state_lock:
            if self._data is None:
                self._data = _load_config_file()
            return self._data

    # --- Write / refresh ---------------------------------------------

    def save_full(self, data: dict) -> None:
        """Atomically persist *data* and notify subscribers."""
        with _write_lock:
            _atomic_write(data)
        self._update_and_notify(data)

    def reload(self) -> dict:
        """Re-read ``config/config.json`` from disk and notify subscribers."""
        data = _load_config_file()
        self._update_and_notify(data)
        return data

    def _update_and_notify(self, data: dict) -> None:
        """Update the cache and fire subscribers.

        Internal — legacy ``save_config`` / ``save_module_field`` call this
        directly after performing their own atomic write to avoid writing
        twice.
        """
        with self._state_lock:
            self._data = data
        self._notify(data)

    # --- Subscribers --------------------------------------------------

    def subscribe(self, callback: Callable[[dict], Any]) -> Callable[[], None]:
        """Register *callback* to be invoked after every save/reload.

        Returns a zero-arg ``unsubscribe()`` helper.
        """
        with self._state_lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._state_lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def _notify(self, data: dict) -> None:
        with self._state_lock:
            listeners = list(self._subscribers)
        for cb in listeners:
            try:
                cb(data)
            except Exception as e:  # noqa: BLE001 — isolate misbehaving subs
                logger.exception("ConfigStore subscriber raised: %s", e)


# Global singleton — import this anywhere you need reactive config access.
config_store = ConfigStore()


# ---------------------------------------------------------------------------
# Legacy function API (kept working via re-export shim)
# ---------------------------------------------------------------------------


def load_full_config() -> dict:
    """Load the complete configuration (fresh read every call)."""
    return _load_config_file()


def load_config(module_name: str | None = None) -> tuple[dict, dict, list[str]]:
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
        with open(CONFIG_PATH, encoding="utf-8") as file:
            data = json.load(file)
        for server_id, server_info in data["servers"].items():
            if str(server_id) in enabled_servers:
                server_info[module_name] = module_config
            else:
                server_info[module_name] = {"enabled": False}
        data["config"] = config
        _atomic_write(data)
    config_store._update_and_notify(data)
    logger.info(
        "Saved config for module %s for servers %s",
        module_name,
        enabled_servers,
    )


def load_discord2name(guild_id: str | int) -> dict:
    """Load the discord2name mapping for a specific guild."""
    data = load_full_config()
    return data.get("servers", {}).get(str(guild_id), {}).get("discord2name", {})


def save_module_field(module_name: str, guild_id: str | int, field: str, value) -> None:
    """Update a single field inside ``servers.<guild_id>.<module_name>``.

    Creates intermediate dicts if missing.
    """
    with _write_lock:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as file:
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
    config_store._update_and_notify(data)
    logger.info("Updated config: servers.%s.%s.%s", guild_id, module_name, field)
