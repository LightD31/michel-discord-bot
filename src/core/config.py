"""Configuration manager for ``config/config.json``.

:class:`ConfigStore` holds the config in memory and every write goes through
it, so readers never see a stale copy. Extensions read it live through
:class:`ModuleConfig` (one per-guild module) and :func:`global_config`.

Writes are atomic (tempfile + ``os.replace`` + ``fsync``) and serialized by a
``threading.Lock`` — the bot runs an asyncio loop and the optional WebUI runs
in a separate uvicorn thread, so a thread-aware primitive is required.
"""

import contextlib
import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

from src.core import logging as _logging

logger = _logging.init_logger(__name__)

CONFIG_PATH = os.path.join("config", "config.json")


# ---------------------------------------------------------------------------
# Low-level filesystem helpers
# ---------------------------------------------------------------------------

_write_lock = threading.Lock()


def _load_config_file() -> dict[str, Any]:
    """Read and parse ``config/config.json``. Returns ``{}`` on failure."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            return cast(dict[str, Any], json.load(file))
    except FileNotFoundError:
        logger.error("Configuration file not found: %s", CONFIG_PATH)
        return {}
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in %s: %s", CONFIG_PATH, e)
        return {}


def _atomic_write(data: dict[str, Any]) -> None:
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
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()
        raise


def config_write_error() -> str | None:
    """Probe whether the config directory accepts new files.

    Atomic writes create a tempfile next to ``config.json``, so they need
    write permission on the *directory* — a bind mount owned by another uid
    breaks saves even when the file itself is readable. Returns a
    human-readable error string, or ``None`` when writable.
    """
    directory = os.path.dirname(CONFIG_PATH) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".writetest-", suffix=".tmp", dir=directory)
        os.close(fd)
        os.unlink(tmp_path)
        return None
    except OSError as e:
        return f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Reactive store
# ---------------------------------------------------------------------------


class ConfigStore:
    """Singleton-style reactive wrapper around ``config/config.json``.

    Purpose
    -------
    The store holds the authoritative in-memory copy, which the dashboard's
    saves and the bot's own writes (``mutate``) update,
    and notifies subscribers on every successful save or reload. Reads through
    :class:`ModuleConfig` and :func:`global_config` therefore follow every
    edit without a reload.

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
    _data: dict[str, Any] | None
    _subscribers: list[Callable[[dict[str, Any]], Any]]
    _state_lock: threading.Lock

    def __new__(cls) -> "ConfigStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = None
            cls._instance._subscribers = []
            cls._instance._state_lock = threading.Lock()
        return cls._instance

    # --- Read ---------------------------------------------------------

    def get(self) -> dict[str, Any]:
        """Return the cached config dict, loading it from disk on first call."""
        with self._state_lock:
            if self._data is None:
                self._data = _load_config_file()
            return self._data

    # --- Write / refresh ---------------------------------------------

    def save_full(self, data: dict[str, Any]) -> None:
        """Atomically persist *data* and notify subscribers."""
        with _write_lock:
            _atomic_write(data)
        self._update_and_notify(data)

    def mutate(self, mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Atomically read-modify-write the config under the write lock.

        ``mutator`` receives a fresh read of the on-disk config and edits it in
        place. Holding ``_write_lock`` across read → mutate → write closes the
        race where two concurrent "load full config, edit, save" sequences
        (e.g. two dashboard requests) silently drop one of the edits.

        Raises :class:`src.core.errors.ConfigError` instead of writing when the
        config file is unreadable or empty — mutating an empty skeleton would
        wipe the real config.
        """
        from src.core.errors import ConfigError

        with _write_lock:
            data = _load_config_file()
            if not data:
                raise ConfigError(
                    f"{CONFIG_PATH} unreadable or empty — refusing to mutate and overwrite it"
                )
            mutator(data)
            _atomic_write(data)
        self._update_and_notify(data)
        return data

    def reload(self) -> dict[str, Any]:
        """Re-read ``config/config.json`` from disk and notify subscribers."""
        data = _load_config_file()
        self._update_and_notify(data)
        return data

    def _update_and_notify(self, data: dict[str, Any]) -> None:
        """Update the cache and fire subscribers.

        Internal — called after the file was written (or read) under the lock.
        """
        with self._state_lock:
            self._data = data
        self._notify(data)

    # --- Subscribers --------------------------------------------------

    def subscribe(self, callback: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """Register *callback* to be invoked after every save/reload.

        Returns a zero-arg ``unsubscribe()`` helper.
        """
        with self._state_lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._state_lock, contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe

    def _notify(self, data: dict[str, Any]) -> None:
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
# Read accessors
# ---------------------------------------------------------------------------


def load_full_config() -> dict[str, Any]:
    """Load the complete configuration (fresh read every call)."""
    return _load_config_file()


def global_config() -> dict[str, Any]:
    """The global sections (the ``config`` key), as the store holds them now.

    Read-only: it is the store's own data.
    """
    data = config_store.get().get("config")
    return data if isinstance(data, dict) else {}


def _guild_key(guild_id: object) -> str:
    return "" if guild_id is None else str(guild_id).strip()


@dataclass(frozen=True)
class ModuleConfig:
    """Live, read-only view of one per-guild module (``servers.<id>.<key>``).

    Every call reads the config store, which the dashboard's saves update, so
    an extension holding one of these sees a change on its next read without
    being reloaded. Declare one per module, next to the schema::

        TRICOUNT = ModuleConfig("moduleTricount")

        @slash_command(..., scopes=guild_scopes(TRICOUNT.guild_ids()))  # fixed at import
        async def cmd(self, ctx):
            currency = TRICOUNT.get(ctx.guild_id).get("currency", "€")      # live

    Command scopes are the one thing taken at import: interactions.py
    registers a command on its guilds when the class is defined, which is why
    switching a module on or off in a guild reloads its extensions.

    Never mutate what it returns: it is the store's own data, shared with
    every other reader. Write through :meth:`ConfigStore.mutate` or the
    dashboard.
    """

    key: str

    def _module(self, server: object) -> dict[str, Any] | None:
        module = server.get(self.key) if isinstance(server, dict) else None
        return module if isinstance(module, dict) and bool(module.get("enabled")) else None

    def items(self) -> list[tuple[str, dict[str, Any]]]:
        """``(guild_id, settings)`` for each guild where the module is on, now."""
        servers = config_store.get().get("servers")
        if not isinstance(servers, dict):
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        for guild_id, server in servers.items():
            module = self._module(server)
            if module is not None:
                out.append((str(guild_id), module))
        return out

    def guild_ids(self) -> list[str]:
        """The guilds where the module is on, now."""
        return [guild_id for guild_id, _ in self.items()]

    def get(self, guild_id: object) -> dict[str, Any]:
        """The module's settings in *guild_id*, or ``{}`` where it is off (or no guild)."""
        servers = config_store.get().get("servers")
        if not isinstance(servers, dict):
            return {}
        return self._module(servers.get(_guild_key(guild_id))) or {}

    def enabled(self, guild_id: object) -> bool:
        """Whether the module is on in *guild_id* (``None``, a DM: never)."""
        servers = config_store.get().get("servers")
        if not isinstance(servers, dict):
            return False
        return self._module(servers.get(_guild_key(guild_id))) is not None


def load_discord2name(guild_id: str | int) -> dict[str, Any]:
    """The guild's Discord id → first name mapping, as the store holds it now."""
    servers = config_store.get().get("servers")
    server = servers.get(_guild_key(guild_id)) if isinstance(servers, dict) else None
    mapping = server.get("discord2name") if isinstance(server, dict) else None
    return mapping if isinstance(mapping, dict) else {}
