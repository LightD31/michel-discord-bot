"""MongoDB I/O for the giveaway feature."""

import os
from datetime import datetime

import pymongo
from bson import ObjectId

from features.giveaway.models import Giveaway
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

COLLECTION = "giveaways"


class GiveawayRepository:
    """Per-guild store for active and historical giveaways."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index(
                [("message_id", pymongo.ASCENDING)], name="message_id_idx", unique=True
            )
            await self._col().create_index(
                [("drawn", pymongo.ASCENDING), ("ends_at", pymongo.ASCENDING)],
                name="ends_at_idx",
            )
        except Exception as e:
            logger.error("Failed to create giveaway indexes for guild %s: %s", self._guild_id, e)

    @staticmethod
    def _doc_to_giveaway(doc: dict) -> Giveaway:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Giveaway(**doc)

    async def add(self, giveaway: Giveaway) -> str:
        try:
            payload = giveaway.model_dump(exclude={"id"})
            result = await self._col().insert_one(payload)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to add giveaway: {e}") from e

    async def get_by_message(self, message_id: str) -> Giveaway | None:
        doc = await self._col().find_one({"message_id": str(message_id)})
        return self._doc_to_giveaway(doc) if doc else None

    async def list_active(self) -> list[Giveaway]:
        cursor = self._col().find({"drawn": False, "cancelled": False}).sort("ends_at", 1)
        docs = await cursor.to_list(length=None)
        return [self._doc_to_giveaway(d) for d in docs]

    async def list_due(self, now: datetime) -> list[Giveaway]:
        cursor = self._col().find({"drawn": False, "cancelled": False, "ends_at": {"$lte": now}})
        docs = await cursor.to_list(length=None)
        return [self._doc_to_giveaway(d) for d in docs]

    async def mark_drawn(self, giveaway_id: str, winners: list[str]) -> None:
        try:
            await self._col().update_one(
                {"_id": ObjectId(giveaway_id)},
                {"$set": {"drawn": True, "winners": winners}},
            )
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to mark giveaway drawn: {e}") from e

    async def mark_cancelled(self, giveaway_id: str) -> None:
        try:
            await self._col().update_one(
                {"_id": ObjectId(giveaway_id)},
                {"$set": {"cancelled": True}},
            )
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to cancel giveaway: {e}") from e

    async def update_winners(self, giveaway_id: str, winners: list[str]) -> None:
        try:
            await self._col().update_one(
                {"_id": ObjectId(giveaway_id)},
                {"$set": {"winners": winners}},
            )
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to update winners: {e}") from e
