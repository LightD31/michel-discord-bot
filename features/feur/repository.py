"""Feur repository — all MongoDB I/O for the feur feature."""

import os

from src.core.db import mongo_manager
from src.core.logging import init_logger
from features.feur.models import FeurStats

logger = init_logger(os.path.basename(__file__))


class FeurRepository:
    def __init__(self, guild_id: str) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "feur_stats")

    async def get_guild_stats(self) -> FeurStats:
        doc = await self._col().find_one({"_id": "guild_total"})
        if doc:
            return FeurStats(
                total=doc.get("total", 0),
                feur=doc.get("feur", 0),
                pour_feur=doc.get("pour_feur", 0),
            )
        return FeurStats()

    async def get_user_stats(self, user_id: str) -> FeurStats:
        doc = await self._col().find_one({"_id": f"user_{user_id}"})
        if doc:
            return FeurStats(
                total=doc.get("total", 0),
                feur=doc.get("feur", 0),
                pour_feur=doc.get("pour_feur", 0),
            )
        return FeurStats()

    async def record_event(self, user_id: str, feur_type: str) -> None:
        """Atomically increment guild-total and per-user counters."""
        await self._col().update_one(
            {"_id": "guild_total"}, {"$inc": {"total": 1, feur_type: 1}}, upsert=True
        )
        await self._col().update_one(
            {"_id": f"user_{user_id}"}, {"$inc": {"total": 1, feur_type: 1}}, upsert=True
        )

    async def get_all_user_totals(self) -> list[tuple[str, int]]:
        """Return (user_id_str, total) pairs for all users in the guild."""
        result = []
        async for doc in self._col().find({"_id": {"$regex": "^user_"}}):
            uid = doc["_id"].replace("user_", "")
            result.append((uid, doc.get("total", 0)))
        return result
