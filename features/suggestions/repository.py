"""MongoDB I/O for the suggestions feature.

One ``suggestions`` collection per guild plus a ``suggestions_meta`` collection
holding a single counter document used to mint per-guild human IDs.
"""

from datetime import datetime

import pymongo
from pymongo import ReturnDocument

from features.suggestions.models import Suggestion, SuggestionStatus, VoteDirection
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger, log_failure

logger = init_logger(__name__)

COLLECTION = "suggestions"
META_COLLECTION = "suggestions_meta"
COUNTER_ID = "counter"


class SuggestionsRepository:
    """Per-guild store for suggestions + auto-incrementing IDs."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    def _meta_col(self):
        return mongo_manager.get_guild_collection(self._guild_id, META_COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index([("id", pymongo.ASCENDING)], unique=True, name="id_idx")
            await self._col().create_index(
                [("message_id", pymongo.ASCENDING)], name="message_id_idx"
            )
            await self._col().create_index([("status", pymongo.ASCENDING)], name="status_idx")
        except Exception as e:
            log_failure(
                logger, "Failed to create suggestions indexes for %s: %s", self._guild_id, e
            )

    @translates_db_errors
    async def next_id(self) -> int:
        doc = await self._meta_col().find_one_and_update(
            {"_id": COUNTER_ID},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["value"])

    @staticmethod
    def _doc_to_suggestion(doc: dict) -> Suggestion:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Suggestion(**doc)

    @translates_db_errors
    async def add(self, suggestion: Suggestion) -> Suggestion:
        result = await self._col().insert_one(suggestion.model_dump(exclude={"object_id"}))
        suggestion.object_id = str(result.inserted_id)
        return suggestion

    @translates_db_errors
    async def get(self, sugg_id: int) -> Suggestion | None:
        doc = await self._col().find_one({"id": sugg_id})
        return self._doc_to_suggestion(doc) if doc else None

    @translates_db_errors
    async def list(self, status: SuggestionStatus | None = None) -> list[Suggestion]:
        query = {"status": status} if status else {}
        cursor = self._col().find(query).sort("created_at", pymongo.DESCENDING)
        return [self._doc_to_suggestion(d) for d in await cursor.to_list(length=None)]

    @translates_db_errors
    async def set_message(self, sugg_id: int, message_id: str) -> None:
        await self._col().update_one({"id": sugg_id}, {"$set": {"message_id": message_id}})

    @translates_db_errors
    async def update_status(
        self,
        sugg_id: int,
        status: SuggestionStatus,
        reason: str | None,
        decided_by: str,
    ) -> None:
        await self._col().update_one(
            {"id": sugg_id},
            {
                "$set": {
                    "status": status,
                    "reason": reason,
                    "decided_by": decided_by,
                    "decided_at": datetime.now(),
                }
            },
        )

    @translates_db_errors
    async def toggle_vote(
        self, sugg_id: int, user_id: str, direction: VoteDirection
    ) -> Suggestion | None:
        """Cast ``direction`` for the user, or withdraw it if that's already their vote.

        One atomic update, so two quick clicks can't both read "no vote yet"
        and leave a vote that should have been withdrawn. Only a pending
        suggestion takes votes: returns ``None`` when the suggestion is missing
        or already decided.
        """
        if not user_id.isdigit():
            raise ValueError(f"Not a Discord user id: {user_id!r}")
        field = f"votes.{user_id}"
        doc = await self._col().find_one_and_update(
            {"id": sugg_id, "status": "pending"},
            [
                {
                    "$set": {
                        field: {"$cond": [{"$eq": [f"${field}", direction]}, "$$REMOVE", direction]}
                    }
                }
            ],
            return_document=ReturnDocument.AFTER,
        )
        return self._doc_to_suggestion(doc) if doc else None
