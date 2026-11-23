"""MongoDB I/O for the giveaway feature."""

from datetime import datetime

import pymongo
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument

from features.giveaway.models import Giveaway
from src.core.db import GuildRepository, IndexSpec, translates_db_errors

COLLECTION = "giveaways"


class GiveawayRepository(GuildRepository):
    """Per-guild store for active and historical giveaways."""

    COLLECTION = COLLECTION

    def indexes(self) -> list[IndexSpec]:
        return [
            ("", [("message_id", pymongo.ASCENDING)], {"name": "message_id_idx", "unique": True}),
            (
                "",
                [("drawn", pymongo.ASCENDING), ("ends_at", pymongo.ASCENDING)],
                {"name": "ends_at_idx"},
            ),
        ]

    @staticmethod
    def _doc_to_giveaway(doc: dict) -> Giveaway:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Giveaway(**doc)

    @staticmethod
    def _oid(giveaway_id: str) -> ObjectId | None:
        """The id a command was given, or None when it isn't one (an ordinary "not found")."""
        try:
            return ObjectId(giveaway_id)
        except (InvalidId, TypeError):
            return None

    @translates_db_errors
    async def add(self, giveaway: Giveaway) -> str:
        result = await self._col().insert_one(giveaway.model_dump(exclude={"id"}))
        return str(result.inserted_id)

    @translates_db_errors
    async def get_by_message(self, message_id: str) -> Giveaway | None:
        doc = await self._col().find_one({"message_id": str(message_id)})
        return self._doc_to_giveaway(doc) if doc else None

    @translates_db_errors
    async def list_active(self) -> list[Giveaway]:
        cursor = self._col().find({"drawn": False, "cancelled": False}).sort("ends_at", 1)
        return [self._doc_to_giveaway(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def list_drawn(self, limit: int) -> list[Giveaway]:
        """The *limit* most recently ended giveaways that were drawn (for a reroll)."""
        cursor = self._col().find({"drawn": True}).sort("ends_at", -1).limit(limit)
        return [self._doc_to_giveaway(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def list_due(self, now: datetime) -> list[Giveaway]:
        cursor = self._col().find({"drawn": False, "cancelled": False, "ends_at": {"$lte": now}})
        return [self._doc_to_giveaway(d) for d in await cursor.to_list()]

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

    @translates_db_errors
    async def _change_entrants(
        self, message_id: str, now: datetime, update: dict
    ) -> Giveaway | None:
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
        return self._doc_to_giveaway(doc) if doc else None

    @translates_db_errors
    async def claim_draw(self, giveaway_id: str) -> Giveaway | None:
        """Mark an open giveaway drawn and return it, entrants included; None when taken.

        One conditional update, so of two draws racing (the scheduler and
        ``/concours tirer`` at the deadline), only one gets the giveaway. Entries
        close with it: the entrants returned are the final ones.
        """
        oid = self._oid(giveaway_id)
        if oid is None:
            return None
        doc = await self._col().find_one_and_update(
            {"_id": oid, "drawn": False, "cancelled": False},
            {"$set": {"drawn": True, "winners": []}},
            return_document=ReturnDocument.AFTER,
        )
        return self._doc_to_giveaway(doc) if doc else None

    @translates_db_errors
    async def release_draw(self, giveaway_id: str) -> None:
        """Give a claimed draw back to the scheduler, counting the failed attempt."""
        oid = self._oid(giveaway_id)
        if oid is not None:
            await self._col().update_one(
                {"_id": oid, "drawn": True},
                {"$set": {"drawn": False}, "$inc": {"draw_failures": 1}},
            )

    @translates_db_errors
    async def mark_cancelled(self, giveaway_id: str) -> bool:
        """Cancel an open giveaway; False when it was drawn or cancelled meanwhile."""
        oid = self._oid(giveaway_id)
        if oid is None:
            return False
        result = await self._col().update_one(
            {"_id": oid, "drawn": False, "cancelled": False}, {"$set": {"cancelled": True}}
        )
        return result.modified_count == 1

    @translates_db_errors
    async def update_winners(self, giveaway_id: str, winners: list[str]) -> None:
        oid = self._oid(giveaway_id)
        if oid is not None:
            await self._col().update_one({"_id": oid}, {"$set": {"winners": winners}})
