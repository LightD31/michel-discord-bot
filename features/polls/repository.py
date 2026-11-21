"""MongoDB I/O for the anonymous and ranked polls (a public poll is Discord's own)."""

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

    @staticmethod
    def _vote_field(user_id: str) -> str:
        # A Discord id (digits only), so safe to put in a field path.
        if not str(user_id).isdigit():
            raise ValueError(f"not a Discord id: {user_id!r}")
        return f"votes.{user_id}"

    @translates_db_errors
    async def set_vote(self, poll_id: str, user_id: str, ranking: list[int]) -> Poll | None:
        """Replace *user_id*'s vote while the poll is open; the updated poll, else ``None``."""
        doc = await self._col().find_one_and_update(
            {"_id": ObjectId(poll_id), "closed": False},
            {"$set": {self._vote_field(user_id): ranking}},
            return_document=pymongo.ReturnDocument.AFTER,
        )
        return self._doc_to_poll(doc) if doc else None

    @translates_db_errors
    async def add_to_ranking(self, poll_id: str, user_id: str, option: int) -> Poll | None:
        """Append *option* to *user_id*'s ranking unless it's in it already.

        One pipeline update, only while the poll is open, returning the updated
        poll (``None`` once it is closed). Reading the ranking, extending it and
        writing it back lost a rank to two quick clicks, and accepted a vote
        after the poll closed.
        """
        field = self._vote_field(user_id)
        current = {"$ifNull": [f"${field}", []]}
        doc = await self._col().find_one_and_update(
            {"_id": ObjectId(poll_id), "closed": False},
            [
                {
                    "$set": {
                        field: {
                            "$cond": [
                                {"$in": [option, current]},
                                current,
                                {"$concatArrays": [current, [option]]},
                            ]
                        }
                    }
                }
            ],
            return_document=pymongo.ReturnDocument.AFTER,
        )
        return self._doc_to_poll(doc) if doc else None

    @translates_db_errors
    async def clear_vote(self, poll_id: str, user_id: str) -> Poll | None:
        """Withdraw *user_id*'s vote while the poll is open; the updated poll, else ``None``."""
        doc = await self._col().find_one_and_update(
            {"_id": ObjectId(poll_id), "closed": False},
            {"$unset": {self._vote_field(user_id): ""}},
            return_document=pymongo.ReturnDocument.AFTER,
        )
        return self._doc_to_poll(doc) if doc else None

    @translates_db_errors
    async def list_due(self, now: datetime) -> list[Poll]:
        cursor = self._col().find({"closed": False, "closes_at": {"$ne": None, "$lte": now}})
        return [self._doc_to_poll(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def edit(
        self, poll_id: str, author_id: str, question: str, options: list[str] | None = None
    ) -> Poll | None:
        """The author's edit of an open poll ("✏️ Modifier"); the updated poll, else ``None``.

        The options change only while nobody has voted (votes point at an
        option's place): with *options*, a poll someone voted on meanwhile is
        left as it was and ``None`` comes back.
        """
        query: dict = {"_id": ObjectId(poll_id), "author_id": str(author_id), "closed": False}
        change: dict = {"question": question}
        if options is not None:
            query["votes"] = {}
            change["options"] = options
        doc = await self._col().find_one_and_update(
            query, {"$set": change}, return_document=pymongo.ReturnDocument.AFTER
        )
        return self._doc_to_poll(doc) if doc else None

    @translates_db_errors
    async def mark_closed(self, poll_id: str) -> None:
        await self._col().update_one({"_id": ObjectId(poll_id)}, {"$set": {"closed": True}})
