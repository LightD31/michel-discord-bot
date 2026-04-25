"""MongoDB I/O for the suggestions feature.

One ``suggestions`` collection per guild plus a ``suggestions_meta`` collection
holding a single counter document used to mint per-guild human IDs.
"""

import os
from datetime import datetime

import pymongo
from pymongo import ReturnDocument

from features.suggestions.models import Suggestion, SuggestionStatus
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

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
            logger.error("Failed to create suggestions indexes for %s: %s", self._guild_id, e)

    async def next_id(self) -> int:
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
            raise DatabaseError(f"Failed to mint suggestion id: {e}") from e

    @staticmethod
    def _doc_to_suggestion(doc: dict) -> Suggestion:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Suggestion(**doc)

    async def add(self, suggestion: Suggestion) -> Suggestion:
        try:
            payload = suggestion.model_dump(exclude={"object_id"})
            result = await self._col().insert_one(payload)
            suggestion.object_id = str(result.inserted_id)
            return suggestion
        except Exception as e:
            logger.error("DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to add suggestion: {e}") from e

    async def get(self, sugg_id: int) -> Suggestion | None:
        try:
            doc = await self._col().find_one({"id": sugg_id})
        except Exception as e:
            logger.error("DB find_one failed: %s", e)
            return None
        return self._doc_to_suggestion(doc) if doc else None

    async def list(self, status: SuggestionStatus | None = None) -> list[Suggestion]:
        try:
            query = {"status": status} if status else {}
            cursor = self._col().find(query).sort("created_at", pymongo.DESCENDING)
            docs = await cursor.to_list(length=None)
            return [self._doc_to_suggestion(d) for d in docs]
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to list suggestions: {e}") from e

    async def set_message(self, sugg_id: int, message_id: str) -> None:
        try:
            await self._col().update_one({"id": sugg_id}, {"$set": {"message_id": message_id}})
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to set suggestion message id: {e}") from e

    async def update_status(
        self,
        sugg_id: int,
        status: SuggestionStatus,
        reason: str | None,
        decided_by: str,
    ) -> None:
        try:
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
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to update suggestion status: {e}") from e

    async def set_vote(self, sugg_id: int, user_id: str, direction: str) -> Suggestion | None:
        """Set/replace a user's vote. Returns the updated suggestion."""
        try:
            doc = await self._col().find_one_and_update(
                {"id": sugg_id},
                {"$set": {f"votes.{user_id}": direction}},
                return_document=ReturnDocument.AFTER,
            )
            return self._doc_to_suggestion(doc) if doc else None
        except Exception as e:
            logger.error("DB set_vote failed: %s", e)
            raise DatabaseError(f"Failed to set vote: {e}") from e

    async def remove_vote(self, sugg_id: int, user_id: str) -> Suggestion | None:
        try:
            doc = await self._col().find_one_and_update(
                {"id": sugg_id},
                {"$unset": {f"votes.{user_id}": ""}},
                return_document=ReturnDocument.AFTER,
            )
            return self._doc_to_suggestion(doc) if doc else None
        except Exception as e:
            logger.error("DB remove_vote failed: %s", e)
            raise DatabaseError(f"Failed to remove vote: {e}") from e
