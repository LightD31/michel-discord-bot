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
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Optional

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from src.core import logging as _logging
from src.core.config import load_config

logger = _logging.init_logger(os.path.basename(__file__))

# Database naming conventions
GLOBAL_DB_NAME = "global"
GUILD_DB_PREFIX = "guild_"


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
        """Export every relevant database (global + guild_*) to JSON files.

        Args:
            backup_dir: Root directory where backups are stored.
            max_backups: Number of timestamped backup folders to keep.
                         Older ones are deleted automatically.

        Returns:
            The path to the newly created backup folder.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest = Path(backup_dir) / timestamp
        dest.mkdir(parents=True, exist_ok=True)

        db_names = await self.client.list_database_names()
        # Only back up our own databases
        relevant = [n for n in db_names if n.startswith(GUILD_DB_PREFIX) or n == GLOBAL_DB_NAME]

        total_docs = 0
        for db_name in relevant:
            db = self.client[db_name]
            col_names = await db.list_collection_names()
            db_dir = dest / db_name
            db_dir.mkdir(parents=True, exist_ok=True)

            for col_name in col_names:
                docs = []
                async for doc in db[col_name].find():
                    # Convert ObjectId and other BSON types to strings
                    doc["_id"] = str(doc["_id"])
                    docs.append(doc)

                out_file = db_dir / f"{col_name}.json"
                out_file.write_text(
                    json.dumps(docs, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                total_docs += len(docs)

            logger.debug("Backed up database '%s' (%d collections)", db_name, len(col_names))

        logger.info(
            "Backup complete: %d databases, %d total documents → %s",
            len(relevant),
            total_docs,
            dest,
        )

        # Prune old backups
        self._prune_backups(backup_dir, max_backups)

        return str(dest)

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
