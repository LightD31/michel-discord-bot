"""MongoDB persistence for AI answer comparisons and their votes.

Comparisons posted in a guild where the AI module is enabled live in that
guild's database; anything else (DMs, user-installed use in other servers)
goes to the shared ``global`` database. Public methods raise
:class:`src.core.errors.DatabaseError`, never driver exceptions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pymongo
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument

from features.ai.models import Comparison
from src.core.db import mongo_manager, translates_db_errors

COLLECTION = "ai_comparisons"
# The leaderboard only needs keys, names and votes — not the answer texts.
_STATS_PROJECTION = {"answers.key": 1, "answers.display_name": 1, "votes": 1}


def _object_id(comparison_id: str) -> ObjectId | None:
    try:
        return ObjectId(comparison_id)
    except (InvalidId, TypeError):
        return None


class AiComparisonRepository:
    """Accessor for one guild's comparisons, or the global ones when ``guild_id`` is None."""

    def __init__(self, guild_id: str | int | None) -> None:
        self.guild_id = str(guild_id) if guild_id is not None else None

    def _col(self) -> Any:
        if self.guild_id is None:
            return mongo_manager.get_global_collection(COLLECTION)
        return mongo_manager.get_guild_collection(self.guild_id, COLLECTION)

    @translates_db_errors
    async def ensure_indexes(self) -> None:
        await self._col().create_index([("message_ids", pymongo.ASCENDING)], name="message_ids")
        await self._col().create_index(
            [("closed", pymongo.ASCENDING), ("closes_at", pymongo.ASCENDING)], name="closes_at"
        )

    @translates_db_errors
    async def create(self, comparison: Comparison) -> str:
        result = await self._col().insert_one(comparison.to_mongo())
        comparison.id = str(result.inserted_id)
        return comparison.id

    @translates_db_errors
    async def set_message_ids(self, comparison_id: str, message_ids: list[str]) -> None:
        oid = _object_id(comparison_id)
        if oid is None:
            return
        await self._col().update_one({"_id": oid}, {"$set": {"message_ids": message_ids}})

    @translates_db_errors
    async def get(self, comparison_id: str) -> Comparison | None:
        oid = _object_id(comparison_id)
        if oid is None:
            return None
        doc = await self._col().find_one({"_id": oid})
        return Comparison.from_mongo(doc) if doc else None

    @translates_db_errors
    async def known_message_ids(self, message_ids: list[str]) -> set[str]:
        """The subset of *message_ids* that belong to a stored comparison."""
        if not message_ids:
            return set()
        wanted = set(message_ids)
        found: set[str] = set()
        cursor = self._col().find({"message_ids": {"$in": message_ids}}, {"message_ids": 1})
        async for doc in cursor:
            found.update(str(m) for m in doc.get("message_ids") or [] if str(m) in wanted)
        return found

    @translates_db_errors
    async def set_vote(self, comparison_id: str, user_id: str, key: str) -> Comparison | None:
        """Record (or change) *user_id*'s vote; ``None`` when missing or already closed."""
        oid = _object_id(comparison_id)
        if oid is None:
            return None
        doc = await self._col().find_one_and_update(
            {"_id": oid, "closed": False},
            {"$set": {f"votes.{user_id}": key}},
            return_document=ReturnDocument.AFTER,
        )
        return Comparison.from_mongo(doc) if doc else None

    @translates_db_errors
    async def due_for_close(self, now: datetime, limit: int = 50) -> list[Comparison]:
        cursor = self._col().find({"closed": False, "closes_at": {"$lte": now}}).limit(limit)
        return [Comparison.from_mongo(doc) async for doc in cursor]

    @translates_db_errors
    async def close(self, comparison_id: str) -> Comparison | None:
        """Mark closed; returns the final state, or ``None`` if someone else closed it first."""
        oid = _object_id(comparison_id)
        if oid is None:
            return None
        doc = await self._col().find_one_and_update(
            {"_id": oid, "closed": False},
            {"$set": {"closed": True}},
            return_document=ReturnDocument.AFTER,
        )
        return Comparison.from_mongo(doc) if doc else None

    @translates_db_errors
    async def list_for_stats(self) -> list[Comparison]:
        """Every comparison that got a vote, oldest first (answer texts omitted)."""
        cursor = (
            self._col()
            .find({"votes": {"$gt": {}}}, _STATS_PROJECTION)
            .sort("_id", pymongo.ASCENDING)
        )
        return [Comparison.from_mongo(doc) async for doc in cursor]
