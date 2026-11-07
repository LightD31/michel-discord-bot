"""MongoDB I/O for the starboard feature — one document per starred message.

The original message id doubles as the document ``_id`` for O(1) lookup.
"""

import pymongo

from features.starboard.models import StarEntry
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger

logger = init_logger(__name__)

COLLECTION = "starboard"


class StarboardRepository:
    """Per-guild store for starboard mappings."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index(
                [("mirror_message_id", pymongo.ASCENDING)], name="mirror_message_id_idx"
            )
        except Exception as e:
            logger.error("Failed to create starboard indexes for %s: %s", self._guild_id, e)

    @staticmethod
    def _doc_to_entry(doc: dict) -> StarEntry:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return StarEntry(**doc)

    @translates_db_errors
    async def get_by_original(self, original_message_id: str) -> StarEntry | None:
        """The mirror of a message, or ``None`` if it isn't on the starboard.

        A database error raises rather than reading as "not starred", which
        would post the message a second time.
        """
        doc = await self._col().find_one({"_id": str(original_message_id)})
        return self._doc_to_entry(doc) if doc else None

    @translates_db_errors
    async def upsert(self, entry: StarEntry) -> None:
        payload = entry.model_dump(exclude={"id"})
        payload["_id"] = entry.original_message_id
        await self._col().update_one(
            {"_id": entry.original_message_id}, {"$set": payload}, upsert=True
        )

    @translates_db_errors
    async def update_count(self, original_message_id: str, count: int) -> None:
        await self._col().update_one({"_id": str(original_message_id)}, {"$set": {"count": count}})

    @translates_db_errors
    async def delete_by_original(self, original_message_id: str) -> int:
        result = await self._col().delete_one({"_id": str(original_message_id)})
        return result.deleted_count
