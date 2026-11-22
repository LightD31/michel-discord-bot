"""Feur repository — all MongoDB I/O for the feur feature.

``feur_stats`` holds the running counters (the guild's and each member's).
``feur_events`` holds one dated document per feur, kept :data:`EVENTS_KEPT`
(a TTL index): enough for the Gazette's feur of the week (P14), no more.
"""

from collections import Counter
from datetime import datetime, timedelta

import pymongo

from features.feur.models import FeurStats
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger

logger = init_logger(__name__)

EVENTS = "feur_events"
EVENTS_KEPT = timedelta(days=8)  # a week, and a day for a late Gazette


class FeurRepository:
    def __init__(self, guild_id: str) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "feur_stats")

    def _events(self):
        return mongo_manager.get_guild_collection(self._guild_id, EVENTS)

    @translates_db_errors
    async def ensure_indexes(self) -> None:
        """The TTL that drops a feur's dated record after :data:`EVENTS_KEPT`."""
        await self._events().create_index(
            [("at", pymongo.ASCENDING)],
            expireAfterSeconds=int(EVENTS_KEPT.total_seconds()),
            name="at_ttl",
        )

    @translates_db_errors
    async def get_guild_stats(self) -> FeurStats:
        doc = await self._col().find_one({"_id": "guild_total"})
        if doc:
            return FeurStats(
                total=doc.get("total", 0),
                feur=doc.get("feur", 0),
                pour_feur=doc.get("pour_feur", 0),
            )
        return FeurStats()

    @translates_db_errors
    async def get_user_stats(self, user_id: str) -> FeurStats:
        doc = await self._col().find_one({"_id": f"user_{user_id}"})
        if doc:
            return FeurStats(
                total=doc.get("total", 0),
                feur=doc.get("feur", 0),
                pour_feur=doc.get("pour_feur", 0),
            )
        return FeurStats()

    @translates_db_errors
    async def record_event(self, user_id: str, feur_type: str, at: datetime) -> None:
        """Count a feur on the guild and the member, and date it for the week's paper."""
        await self._col().update_one(
            {"_id": "guild_total"}, {"$inc": {"total": 1, feur_type: 1}}, upsert=True
        )
        await self._col().update_one(
            {"_id": f"user_{user_id}"}, {"$inc": {"total": 1, feur_type: 1}}, upsert=True
        )
        await self._events().insert_one({"user_id": str(user_id), "kind": feur_type, "at": at})

    @translates_db_errors
    async def counts_between(self, start: datetime, end: datetime) -> Counter[str]:
        """Feurs per member from *start* (included) to *end* (excluded)."""
        pipeline = [
            {"$match": {"at": {"$gte": start, "$lt": end}}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        ]
        rows = await (await self._events().aggregate(pipeline)).to_list()
        return Counter({str(r["_id"]): int(r["count"]) for r in rows})

    @translates_db_errors
    async def get_all_user_totals(self) -> list[tuple[str, int]]:
        """Return (user_id_str, total) pairs for all users in the guild."""
        result = []
        async for doc in self._col().find({"_id": {"$regex": "^user_"}}):
            uid = doc["_id"].replace("user_", "")
            result.append((uid, doc.get("total", 0)))
        return result
