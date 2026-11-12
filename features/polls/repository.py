"""MongoDB I/O for the button-based polls feature."""

from datetime import datetime

import pymongo
from bson import ObjectId

from features.polls.models import Poll
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger, log_failure

logger = init_logger(__name__)

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
            log_failure(logger, "Failed to create poll indexes for guild %s: %s", self._guild_id, e)

    @staticmethod
    def _doc_to_poll(doc: dict) -> Poll:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Poll(**doc)

    @translates_db_errors
    async def add(self, poll: Poll) -> str:
        result = await self._col().insert_one(poll.model_dump(exclude={"id"}))
        return str(result.inserted_id)

    @translates_db_errors
    async def get_by_message(self, message_id: str) -> Poll | None:
        doc = await self._col().find_one({"message_id": str(message_id)})
        return self._doc_to_poll(doc) if doc else None

    @translates_db_errors
    async def set_vote(self, poll_id: str, user_id: str, ranking: list[int]) -> None:
        await self._col().update_one(
            {"_id": ObjectId(poll_id)}, {"$set": {f"votes.{user_id}": ranking}}
        )

    @translates_db_errors
    async def clear_vote(self, poll_id: str, user_id: str) -> None:
        await self._col().update_one(
            {"_id": ObjectId(poll_id)}, {"$unset": {f"votes.{user_id}": ""}}
        )

    @translates_db_errors
    async def list_open_reaction_polls(self, author_id: str, limit: int) -> list[Poll]:
        """The *limit* newest open reaction polls *author_id* posted (``/editpoll``)."""
        cursor = (
            self._col()
            .find({"author_id": str(author_id), "mode": "reaction", "closed": False})
            .sort("_id", -1)
            .limit(limit)
        )
        return [self._doc_to_poll(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def list_due(self, now: datetime) -> list[Poll]:
        cursor = self._col().find({"closed": False, "closes_at": {"$ne": None, "$lte": now}})
        return [self._doc_to_poll(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def set_options(self, poll_id: str, options: list[str], emojis: list[str]) -> None:
        """Replace a stored poll's options (``/editpoll``)."""
        await self._col().update_one(
            {"_id": ObjectId(poll_id)}, {"$set": {"options": options, "emojis": emojis}}
        )

    @translates_db_errors
    async def mark_closed(self, poll_id: str) -> None:
        await self._col().update_one({"_id": ObjectId(poll_id)}, {"$set": {"closed": True}})
