"""
Global MongoDB manager using motor (async driver).

Architecture:
    - Each Discord guild gets its own database: ``guild_{guild_id}``
      with one collection per module (birthday, xp, tricount_groups, …).
    - Truly global data (olympics, task_reminders, …) lives in the
      ``global`` database.

Usage:
    from src.mongodb import mongo_manager

    # Per-guild helpers
    db  = mongo_manager.get_guild_db("123456789")
    col = mongo_manager.get_guild_collection("123456789", "birthday")

    # Global helpers
    col = mongo_manager.get_global_collection("olympics_state")

    # Low-level / legacy access
    db  = mongo_manager["some_db"]["some_collection"]
"""

import os
from typing import Optional, Union

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

# Database naming conventions
GLOBAL_DB_NAME = "global"
GUILD_DB_PREFIX = "guild_"


class MongoManager:
    """Singleton-style global MongoDB connection manager using motor (async)."""

    _instance: Optional["MongoManager"] = None
    _client: Optional[AsyncIOMotorClient] = None
    _url: Optional[str] = None

    def __new__(cls) -> "MongoManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_client(self) -> AsyncIOMotorClient:
        """Lazily create the motor client on first access."""
        if self._client is None:
            if self._url is None:
                try:
                    config, _, _ = load_config()
                    self._url = config.get("mongodb", {}).get("url", "")
                except Exception as e:
                    logger.error("Failed to load MongoDB URL from config: %s", e)
                    self._url = ""

            if not self._url:
                raise RuntimeError(
                    "MongoDB URL is not configured. "
                    "Set 'mongodb.url' in your configuration."
                )

            self._client = AsyncIOMotorClient(
                self._url,
                serverSelectionTimeoutMS=5000,
            )
            logger.info("Motor async MongoDB client created.")
        return self._client

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def client(self) -> AsyncIOMotorClient:
        """Return the underlying motor client (creates it if needed)."""
        return self._ensure_client()

    # --- Per-guild helpers -------------------------------------------

    def get_guild_db(self, guild_id: Union[str, int]) -> AsyncIOMotorDatabase:
        """Return the database for a specific guild."""
        return self.client[f"{GUILD_DB_PREFIX}{guild_id}"]

    def get_guild_collection(
        self, guild_id: Union[str, int], collection_name: str
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
        """Close the motor client."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.info("MongoDB connection closed.")

    # ------------------------------------------------------------------
    # dict-like access:  mongo_manager["dbname"]["collection"]
    # ------------------------------------------------------------------

    def __getitem__(self, db_name: str) -> AsyncIOMotorDatabase:
        return self.get_database(db_name)


# Global singleton – import this everywhere
mongo_manager = MongoManager()
