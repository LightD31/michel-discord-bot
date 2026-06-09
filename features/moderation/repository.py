"""MongoDB I/O for the moderation feature.

One ``infractions`` collection per guild plus an ``infractions_meta`` collection
holding a single counter document used to mint per-guild case numbers.
"""

import os

import pymongo
from pymongo import ReturnDocument

from features.moderation.models import Infraction, InfractionType
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

COLLECTION = "infractions"
META_COLLECTION = "infractions_meta"
COUNTER_ID = "counter"


class ModerationRepository:
    """Per-guild store for infractions + auto-incrementing case numbers."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    def _meta_col(self):
        return mongo_manager.get_guild_collection(self._guild_id, META_COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index([("id", pymongo.ASCENDING)], unique=True, name="id_idx")
            await self._col().create_index([("user_id", pymongo.ASCENDING)], name="user_id_idx")
            await self._col().create_index([("type", pymongo.ASCENDING)], name="type_idx")
            await self._col().create_index([("active", pymongo.ASCENDING)], name="active_idx")
        except Exception as e:
            logger.error("Failed to create infraction indexes for %s: %s", self._guild_id, e)

    async def next_case_id(self) -> int:
        try:
            doc = await self._meta_col().find_one_and_update(
                {"_id": COUNTER_ID},
                {"$inc": {"value": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            return int(doc["value"])
        except Exception as e:
            logger.error("Counter increment failed: %s", e)
            raise DatabaseError(f"Failed to mint case id: {e}") from e

    @staticmethod
    def _doc_to_infraction(doc: dict) -> Infraction:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Infraction(**doc)

    async def add(self, infraction: Infraction) -> Infraction:
        try:
            payload = infraction.model_dump(exclude={"object_id"})
            result = await self._col().insert_one(payload)
            infraction.object_id = str(result.inserted_id)
            return infraction
        except Exception as e:
            logger.error("DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to add infraction: {e}") from e

    async def get(self, case_id: int) -> Infraction | None:
        try:
            doc = await self._col().find_one({"id": case_id})
        except Exception as e:
            logger.error("DB find_one failed: %s", e)
            return None
        return self._doc_to_infraction(doc) if doc else None

    async def list_for_user(self, user_id: str) -> list[Infraction]:
        try:
            cursor = (
                self._col().find({"user_id": str(user_id)}).sort("created_at", pymongo.DESCENDING)
            )
            docs = await cursor.to_list(length=None)
            return [self._doc_to_infraction(d) for d in docs]
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to list infractions: {e}") from e

    async def list(
        self,
        *,
        type: InfractionType | None = None,
        user_id: str | None = None,
        active: bool | None = None,
        limit: int | None = None,
    ) -> list[Infraction]:
        try:
            query: dict = {}
            if type is not None:
                query["type"] = type
            if user_id is not None:
                query["user_id"] = str(user_id)
            if active is not None:
                query["active"] = active
            cursor = self._col().find(query).sort("created_at", pymongo.DESCENDING)
            if limit:
                cursor = cursor.limit(limit)
            docs = await cursor.to_list(length=limit)
            return [self._doc_to_infraction(d) for d in docs]
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to list infractions: {e}") from e

    async def set_active(self, case_id: int, active: bool) -> bool:
        """Flip a case's active flag. Returns True if a document was modified."""
        try:
            result = await self._col().update_one({"id": case_id}, {"$set": {"active": active}})
            return result.matched_count > 0
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to update infraction: {e}") from e

    async def count_active_warnings(self, user_id: str) -> int:
        try:
            return await self._col().count_documents(
                {"user_id": str(user_id), "type": "warn", "active": True}
            )
        except Exception as e:
            logger.error("DB count failed: %s", e)
            return 0
