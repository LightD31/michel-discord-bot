"""MongoDB I/O for RSS feed bookkeeping — one document per ``feed_id`` per guild."""

import os
from datetime import datetime

from features.rss.models import RssFeedState
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

COLLECTION = "rss"
# How many seen entry ids to keep per feed. Larger = more dedupe history but
# more wasted disk; 200 covers a few weeks for typical news feeds.
MAX_SEEN_IDS = 200


class RssRepository:
    """Per-guild store for RSS feed state."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    @staticmethod
    def _doc_to_state(doc: dict) -> RssFeedState:
        return RssFeedState(**doc)

    async def get(self, feed_id: str) -> RssFeedState | None:
        try:
            doc = await self._col().find_one({"_id": feed_id})
        except Exception as e:
            logger.error("DB find_one failed: %s", e)
            raise DatabaseError(f"Failed to load RSS state: {e}") from e
        return self._doc_to_state(doc) if doc else None

    async def initialize(self, feed_id: str, seen_ids: list[str]) -> None:
        """Mark a feed as initialized — seed dedupe set without posting backlog."""
        bounded = seen_ids[:MAX_SEEN_IDS]
        try:
            await self._col().update_one(
                {"_id": feed_id},
                {
                    "$set": {
                        "seen_ids": bounded,
                        "initialized": True,
                        "last_poll_at": datetime.now(),
                        "last_error": None,
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logger.error("DB upsert failed: %s", e)
            raise DatabaseError(f"Failed to initialize RSS state: {e}") from e

    async def record_seen(self, feed_id: str, new_ids: list[str]) -> None:
        """Prepend *new_ids* to the dedupe set, capped at ``MAX_SEEN_IDS``.

        Mongo's ``$push`` with ``$position: 0`` + ``$slice`` is atomic, so
        concurrent polls (shouldn't happen, but defensive) can't corrupt it.
        """
        if not new_ids:
            return
        try:
            await self._col().update_one(
                {"_id": feed_id},
                {
                    "$push": {
                        "seen_ids": {
                            "$each": new_ids,
                            "$position": 0,
                            "$slice": MAX_SEEN_IDS,
                        }
                    },
                    "$set": {"last_poll_at": datetime.now(), "last_error": None},
                },
                upsert=True,
            )
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to record seen RSS ids: {e}") from e

    async def record_error(self, feed_id: str, message: str) -> None:
        try:
            await self._col().update_one(
                {"_id": feed_id},
                {"$set": {"last_poll_at": datetime.now(), "last_error": message}},
                upsert=True,
            )
        except Exception as e:
            logger.warning("Could not record RSS error for %s: %s", feed_id, e)

    async def delete(self, feed_id: str) -> int:
        try:
            result = await self._col().delete_one({"_id": feed_id})
            return result.deleted_count
        except Exception as e:
            logger.error("DB delete_one failed: %s", e)
            raise DatabaseError(f"Failed to delete RSS state: {e}") from e
