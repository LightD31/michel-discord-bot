"""
Global MongoDB manager using motor (async driver).

Moved from ``src/mongodb.py`` as part of the ``src/core/`` restructure. The old
module path remains as a re-export shim.

Architecture:
    - Each Discord guild gets its own database: ``guild_{guild_id}``
      with one collection per module (birthday, xp, tricount_groups, …).
    - Truly global data (olympics, task_reminders, …) lives in the
      ``global`` database.

Usage::

    from src.core.db import mongo_manager

    # Per-guild helpers
    db  = mongo_manager.get_guild_db("123456789")
    col = mongo_manager.get_guild_collection("123456789", "birthday")

    # Global helpers
    col = mongo_manager.get_global_collection("olympics_state")

    # Low-level / legacy access
    db  = mongo_manager["some_db"]["some_collection"]
"""

import asyncio
import contextlib
import functools
import shutil
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Optional

import pymongo.errors
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from src.core import backup_format
from src.core import config as _config
from src.core import logging as _logging
from src.core.config import load_config
from src.core.errors import DatabaseError
from src.core.health import health

logger = _logging.init_logger(__name__)

# Database naming conventions
GLOBAL_DB_NAME = "global"
GUILD_DB_PREFIX = "guild_"

# (database, collection) pairs never written to a backup. Dashboard sessions are
# bearer credentials: restoring one is useless, leaking one is not.
BACKUP_EXCLUDED_COLLECTIONS: frozenset[tuple[str, str]] = frozenset(
    {(GLOBAL_DB_NAME, "webui_sessions")}
)
# Name of the copy of ``config/config.json`` stored next to each backup.
BACKUP_CONFIG_FILENAME = "config.json"
# Documents handed to the writer thread at a time: bounds memory per collection.
BACKUP_BATCH_SIZE = 1000


def translates_db_errors[**P, R](func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Re-raise ``pymongo`` errors from a repository method as :class:`DatabaseError`.

    Repositories under ``features/<name>/`` are the only layer allowed to touch
    the driver, so this is where driver exceptions stop. Extensions catch
    :class:`~src.core.errors.DatabaseError` and never need to import
    ``pymongo`` to handle a Mongo outage.

    Usage::

        class ThingRepository:
            @translates_db_errors
            async def load(self) -> dict | None:
                return await self._col().find_one({"_id": "x"})
    """

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except pymongo.errors.PyMongoError as e:
            raise DatabaseError(str(e)) from e

    return wrapper


class MongoManager:
    """Singleton-style global MongoDB connection manager using motor (async).

    Keeps one motor client *per event loop*: motor pins each client to the
    loop that first uses it, so sharing a single client between the bot loop
    and the Web UI's uvicorn loop (daemon thread) raises ``got Future
    attached to a different loop``. In practice there are at most two
    clients (bot + webui).
    """

    _instance: Optional["MongoManager"] = None
    _clients: ClassVar[dict[asyncio.AbstractEventLoop | None, AsyncIOMotorClient]] = {}
    _url: str | None = None

    def __new__(cls) -> "MongoManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @staticmethod
    def _current_loop() -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    def _ensure_client(self) -> AsyncIOMotorClient:
        """Lazily create the motor client bound to the current event loop."""
        loop = self._current_loop()
        client = self._clients.get(loop)
        if client is None:
            if self._url is None:
                try:
                    config, _, _ = load_config()
                    self._url = config.get("mongodb", {}).get("url", "")
                except Exception as e:
                    logger.error("Failed to load MongoDB URL from config: %s", e)
                    self._url = ""

            if not self._url:
                raise RuntimeError(
                    "MongoDB URL is not configured. Set 'mongodb.url' in your configuration."
                )

            client = AsyncIOMotorClient(
                self._url,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                socketTimeoutMS=60000,
                maxPoolSize=50,
                minPoolSize=1,
                maxIdleTimeMS=300000,
            )
            self._clients[loop] = client
            logger.info("Motor async MongoDB client created (%d active).", len(self._clients))
        return client

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def client(self) -> AsyncIOMotorClient:
        """Return the underlying motor client (creates it if needed)."""
        return self._ensure_client()

    # --- Per-guild helpers -------------------------------------------

    def get_guild_db(self, guild_id: str | int) -> AsyncIOMotorDatabase:
        """Return the database for a specific guild."""
        return self.client[f"{GUILD_DB_PREFIX}{guild_id}"]

    def get_guild_collection(
        self, guild_id: str | int, collection_name: str
    ) -> AsyncIOMotorCollection:
        """Return a collection inside a guild's database."""
        return self.client[f"{GUILD_DB_PREFIX}{guild_id}"][collection_name]

    # --- Global helpers ----------------------------------------------

    @property
    def global_db(self) -> AsyncIOMotorDatabase:
        """Return the global database (for non-guild-specific data)."""
        return self.client[GLOBAL_DB_NAME]

    def get_global_collection(self, collection_name: str) -> AsyncIOMotorCollection:
        """Return a collection in the global database."""
        return self.client[GLOBAL_DB_NAME][collection_name]

    # --- Low-level / legacy helpers ----------------------------------

    def get_database(self, name: str) -> AsyncIOMotorDatabase:
        """Return a motor database by name."""
        return self.client[name]

    def get_collection(self, db_name: str, collection_name: str) -> AsyncIOMotorCollection:
        """Return a motor collection."""
        return self.client[db_name][collection_name]

    async def ping(self) -> bool:
        """Test the connection. Returns True on success."""
        try:
            await self.client.admin.command("ping")
            logger.info("MongoDB ping successful.")
            return True
        except Exception as e:
            logger.error("MongoDB ping failed: %s", e)
            return False

    async def close(self) -> None:
        """Close every motor client (one per event loop)."""
        if self._clients:
            for client in self._clients.values():
                client.close()
            self._clients.clear()
            logger.info("MongoDB connection(s) closed.")

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    async def backup_all(
        self,
        backup_dir: str = "data/backups",
        max_backups: int = 7,
    ) -> str:
        """Export every relevant database (global + guild_*) to a backup folder.

        Each collection becomes a gzip-compressed Extended JSON Lines file (see
        :mod:`src.core.backup_format`), written in batches on a worker thread so
        neither the whole collection nor the file I/O sits on the event loop.
        Collections in :data:`BACKUP_EXCLUDED_COLLECTIONS` are skipped. A copy of
        ``config/config.json`` (every guild's configuration, which lives outside
        MongoDB) is saved alongside, readable by its owner only, and a manifest
        records how many documents each collection holds.

        Args:
            backup_dir: Root directory where backups are stored.
            max_backups: Number of timestamped backup folders to keep.
                         Older ones are deleted automatically.

        Returns:
            The path to the newly created backup folder.
        """
        timestamp = datetime.now().strftime(backup_format.BACKUP_TIMESTAMP_FORMAT)
        dest = Path(backup_dir) / timestamp
        dest.mkdir(parents=True, exist_ok=True)

        db_names = await self.client.list_database_names()
        # Only back up our own databases
        relevant = [n for n in db_names if n.startswith(GUILD_DB_PREFIX) or n == GLOBAL_DB_NAME]

        counts: dict[str, dict[str, int]] = {}
        for db_name in relevant:
            db = self.client[db_name]
            counts[db_name] = {}
            for col_name in await db.list_collection_names():
                if (db_name, col_name) in BACKUP_EXCLUDED_COLLECTIONS:
                    continue
                counts[db_name][col_name] = await self._dump_collection(
                    db[col_name], backup_format.collection_path(dest, db_name, col_name)
                )
            logger.debug("Backed up database '%s' (%d collections)", db_name, len(counts[db_name]))

        await asyncio.to_thread(backup_format.write_manifest, dest, timestamp, counts)
        self._copy_config(dest)
        health.record_backup()

        logger.info(
            "Backup complete: %d databases, %d total documents → %s",
            len(relevant),
            sum(sum(c.values()) for c in counts.values()),
            dest,
        )

        # Prune old backups
        self._prune_backups(backup_dir, max_backups)

        return str(dest)

    @staticmethod
    async def _dump_collection(collection: Any, path: Path) -> int:
        """Stream *collection* into *path*, :data:`BACKUP_BATCH_SIZE` documents at a time."""
        writer = await asyncio.to_thread(backup_format.CollectionWriter, path)
        try:
            batch: list[dict[str, Any]] = []
            async for doc in collection.find():
                batch.append(doc)
                if len(batch) >= BACKUP_BATCH_SIZE:
                    await asyncio.to_thread(writer.write, batch)
                    batch = []
            if batch:
                await asyncio.to_thread(writer.write, batch)
        finally:
            await asyncio.to_thread(writer.close)
        return writer.count

    @staticmethod
    def _copy_config(dest: Path) -> None:
        """Copy ``config/config.json`` into the backup folder (owner-only permissions)."""
        source = Path(_config.CONFIG_PATH)
        if not source.is_file():
            logger.warning("No %s to back up", source)
            return
        target = dest / BACKUP_CONFIG_FILENAME
        try:
            shutil.copyfile(source, target)
        except OSError as e:
            logger.error("Could not back up %s: %s", source, e)
            return
        with contextlib.suppress(OSError):
            target.chmod(0o600)

    @staticmethod
    def _prune_backups(backup_dir: str, max_backups: int) -> None:
        """Keep only the *max_backups* most recent backup folders."""
        root = Path(backup_dir)
        if not root.is_dir():
            return
        folders = sorted(
            [f for f in root.iterdir() if f.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )
        for old in folders[max_backups:]:
            shutil.rmtree(old, ignore_errors=True)
            logger.info("Pruned old backup: %s", old)

    # ------------------------------------------------------------------
    # dict-like access:  mongo_manager["dbname"]["collection"]
    # ------------------------------------------------------------------

    def __getitem__(self, db_name: str) -> AsyncIOMotorDatabase:
        return self.get_database(db_name)


# Global singleton — import this everywhere.
mongo_manager = MongoManager()
