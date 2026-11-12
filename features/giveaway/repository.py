"""MongoDB I/O for the giveaway feature."""

from datetime import datetime

import pymongo
from bson import ObjectId
from pymongo import ReturnDocument

from features.giveaway.models import Giveaway
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger, log_failure

logger = init_logger(__name__)

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
            log_failure(
                logger, "Failed to create giveaway indexes for guild %s: %s", self._guild_id, e
            )

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
            log_failure(logger, "DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to add giveaway: {e}") from e

    async def get_by_message(self, message_id: str) -> Giveaway | None:
        doc = await self._col().find_one({"message_id": str(message_id)})
        return self._doc_to_giveaway(doc) if doc else None

    async def list_active(self) -> list[Giveaway]:
        cursor = self._col().find({"drawn": False, "cancelled": False}).sort("ends_at", 1)
        docs = await cursor.to_list()
        return [self._doc_to_giveaway(d) for d in docs]

    async def list_drawn(self, limit: int) -> list[Giveaway]:
        """The *limit* most recently ended giveaways that were drawn (for a reroll)."""
        cursor = self._col().find({"drawn": True}).sort("ends_at", -1).limit(limit)
        return [self._doc_to_giveaway(d) for d in await cursor.to_list()]

    async def list_due(self, now: datetime) -> list[Giveaway]:
        cursor = self._col().find({"drawn": False, "cancelled": False, "ends_at": {"$lte": now}})
        docs = await cursor.to_list()
        return [self._doc_to_giveaway(d) for d in docs]

    async def add_entrant(self, message_id: str, user_id: str, now: datetime) -> Giveaway | None:
        """Enter *user_id* in the giveaway of *message_id* if it is still open at *now*.

        One atomic update. Returns the giveaway as it was *before* the click, so
        the caller can tell a new entry from a repeated one, or None when no
        open giveaway is on that message (drawn, cancelled, past its end).
        """
        return await self._change_entrants(message_id, now, {"$addToSet": {"entrants": user_id}})

    async def remove_entrant(self, message_id: str, user_id: str, now: datetime) -> Giveaway | None:
        """Withdraw *user_id* from the open giveaway of *message_id*; see :meth:`add_entrant`."""
        return await self._change_entrants(message_id, now, {"$pull": {"entrants": user_id}})

    async def _change_entrants(
        self, message_id: str, now: datetime, update: dict
    ) -> Giveaway | None:
        try:
            doc = await self._col().find_one_and_update(
                {
                    "message_id": str(message_id),
                    "drawn": False,
                    "cancelled": False,
                    "ends_at": {"$gt": now},
                },
                update,
                return_document=ReturnDocument.BEFORE,
            )
        except Exception as e:
            log_failure(logger, "DB find_one_and_update failed: %s", e)
            raise DatabaseError(f"Failed to update giveaway entrants: {e}") from e
        return self._doc_to_giveaway(doc) if doc else None

    async def claim_draw(self, giveaway_id: str) -> Giveaway | None:
        """Mark an open giveaway drawn and return it, entrants included; None when taken.

        One conditional update, so of two draws racing (the scheduler and
        ``/giveaway end`` at the deadline), only one gets the giveaway. Entries
        close with it: the entrants returned are the final ones.
        """
        try:
            doc = await self._col().find_one_and_update(
                {"_id": ObjectId(giveaway_id), "drawn": False, "cancelled": False},
                {"$set": {"drawn": True, "winners": []}},
                return_document=ReturnDocument.AFTER,
            )
        except Exception as e:
            log_failure(logger, "DB find_one_and_update failed: %s", e)
            raise DatabaseError(f"Failed to claim giveaway draw: {e}") from e
        return self._doc_to_giveaway(doc) if doc else None

    async def release_draw(self, giveaway_id: str) -> None:
        """Give a claimed draw back to the scheduler, counting the failed attempt."""
        try:
            await self._col().update_one(
                {"_id": ObjectId(giveaway_id), "drawn": True},
                {"$set": {"drawn": False}, "$inc": {"draw_failures": 1}},
            )
        except Exception as e:
            log_failure(logger, "DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to release giveaway draw: {e}") from e

    async def mark_cancelled(self, giveaway_id: str) -> bool:
        """Cancel an open giveaway; False when it was drawn or cancelled meanwhile."""
        try:
            result = await self._col().update_one(
                {"_id": ObjectId(giveaway_id), "drawn": False, "cancelled": False},
                {"$set": {"cancelled": True}},
            )
        except Exception as e:
            log_failure(logger, "DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to cancel giveaway: {e}") from e
        return result.modified_count == 1

    async def update_winners(self, giveaway_id: str, winners: list[str]) -> None:
        try:
            await self._col().update_one(
                {"_id": ObjectId(giveaway_id)},
                {"$set": {"winners": winners}},
            )
        except Exception as e:
            log_failure(logger, "DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to update winners: {e}") from e
