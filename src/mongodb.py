"""
Global MongoDB manager using motor (async driver).

Usage:
    from src.mongodb import mongo_manager

    # Get a database
    db = mongo_manager.get_database("Playlist")

    # Get a collection
    collection = mongo_manager.get_collection("Playlist", "birthday")

    # Or use the shorthand
    collection = mongo_manager["Playlist"]["birthday"]
"""

import os
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))


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
