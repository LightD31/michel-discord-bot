"""MongoDB I/O for the starboard feature — one document per starred message.

The original message id doubles as the document ``_id`` for O(1) lookup.
"""

import pymongo

from features.starboard.models import StarEntry
from src.core.db import GuildRepository, IndexSpec, translates_db_errors
from src.core.logging import init_logger

logger = init_logger(__name__)

COLLECTION = "starboard"


class StarboardRepository(GuildRepository):
    """Per-guild store for starboard mappings."""

    COLLECTION = COLLECTION

    def indexes(self) -> list[IndexSpec]:
        return [
            ("", [("mirror_message_id", pymongo.ASCENDING)], {"name": "mirror_message_id_idx"}),
        ]

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
