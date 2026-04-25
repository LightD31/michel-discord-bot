"""MongoDB I/O for the button-based polls feature."""

import os
from datetime import datetime

import pymongo
from bson import ObjectId

from features.polls.models import Poll
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

COLLECTION = "polls"


class PollRepository:
    """Per-guild store for button-based polls."""

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
                [("closed", pymongo.ASCENDING), ("closes_at", pymongo.ASCENDING)],
                name="closes_at_idx",
            )
        except Exception as e:
            logger.error("Failed to create poll indexes for guild %s: %s", self._guild_id, e)

    @staticmethod
    def _doc_to_poll(doc: dict) -> Poll:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Poll(**doc)

    async def add(self, poll: Poll) -> str:
        try:
            payload = poll.model_dump(exclude={"id"})
            result = await self._col().insert_one(payload)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to add poll: {e}") from e

    async def get_by_message(self, message_id: str) -> Poll | None:
        doc = await self._col().find_one({"message_id": str(message_id)})
        return self._doc_to_poll(doc) if doc else None

    async def set_vote(self, poll_id: str, user_id: str, ranking: list[int]) -> None:
        try:
            await self._col().update_one(
                {"_id": ObjectId(poll_id)},
                {"$set": {f"votes.{user_id}": ranking}},
            )
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to record vote: {e}") from e

    async def clear_vote(self, poll_id: str, user_id: str) -> None:
        try:
            await self._col().update_one(
                {"_id": ObjectId(poll_id)},
                {"$unset": {f"votes.{user_id}": ""}},
            )
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to clear vote: {e}") from e

    async def list_due(self, now: datetime) -> list[Poll]:
        cursor = self._col().find(
            {"closed": False, "closes_at": {"$ne": None, "$lte": now}}
        )
        docs = await cursor.to_list(length=None)
        return [self._doc_to_poll(d) for d in docs]

    async def mark_closed(self, poll_id: str) -> None:
        await self._col().update_one(
            {"_id": ObjectId(poll_id)}, {"$set": {"closed": True}}
        )
