"""MongoDB I/O for the suggestions feature.

One ``suggestions`` collection per guild plus a ``suggestions_meta`` collection
holding a single counter document used to mint per-guild human IDs.
"""

from __future__ import annotations

import builtins

import pymongo
from pymongo import ReturnDocument

from features.suggestions.models import Suggestion, SuggestionStatus, VoteDirection
from src.core import clock
from src.core.db import GuildRepository, IndexSpec, translates_db_errors
from src.core.logging import init_logger

logger = init_logger(__name__)

COLLECTION = "suggestions"
META_COLLECTION = "suggestions_meta"
COUNTER_ID = "counter"


class SuggestionsRepository(GuildRepository):
    """Per-guild store for suggestions + auto-incrementing IDs."""

    COLLECTION = COLLECTION

    def _meta_col(self):
        return self._col(META_COLLECTION)

    def indexes(self) -> list[IndexSpec]:
        return [
            ("", [("id", pymongo.ASCENDING)], {"unique": True, "name": "id_idx"}),
            ("", [("message_id", pymongo.ASCENDING)], {"name": "message_id_idx"}),
            ("", [("status", pymongo.ASCENDING)], {"name": "status_idx"}),
        ]

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
        return [self._doc_to_suggestion(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def set_message(self, sugg_id: int, message_id: str) -> None:
        await self._col().update_one({"id": sugg_id}, {"$set": {"message_id": message_id}})

    @translates_db_errors
    async def set_thread(self, sugg_id: int, thread_id: str) -> None:
        await self._col().update_one({"id": sugg_id}, {"$set": {"thread_id": thread_id}})

    @translates_db_errors
    async def list_in(
        self, statuses: builtins.list[SuggestionStatus], limit: int
    ) -> builtins.list[Suggestion]:
        """The newest *limit* suggestions in one of *statuses* (the staff autocomplete)."""
        cursor = (
            self._col()
            .find({"status": {"$in": list(statuses)}})
            .sort("created_at", pymongo.DESCENDING)
            .limit(limit)
        )
        return [self._doc_to_suggestion(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def decide(
        self,
        sugg_id: int,
        status: SuggestionStatus,
        reason: str | None,
        decided_by: str,
        *,
        from_statuses: tuple[SuggestionStatus, ...],
    ) -> Suggestion | None:
        """Record a staff decision, if the suggestion is still in one of *from_statuses*.

        Two moderators clicking at once decide once: the second finds the
        status moved on and gets ``None``, as for a suggestion that is gone.
        """
        doc = await self._col().find_one_and_update(
            {"id": sugg_id, "status": {"$in": list(from_statuses)}},
            {
                "$set": {
                    "status": status,
                    "reason": reason,
                    "decided_by": decided_by,
                    "decided_at": clock.now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return self._doc_to_suggestion(doc) if doc else None

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
