"""Members who left a server, forgotten there 30 days later (F10).

When a member leaves, their data on that server is scheduled for deletion
(``global.member_purges``, one document per guild and member): what is theirs
alone is deleted, what they share is anonymised, as ``/mes-donnees supprimer``
does but for that one server. Coming back within :data:`PURGE_DELAY` cancels
it, so a member who rejoins the next day keeps their XP.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pymongo

from src.core.db import mongo_manager, translates_db_errors

COLLECTION = "member_purges"
PURGE_DELAY = timedelta(days=30)


def _key(guild_id: str, user_id: str) -> str:
    return f"{guild_id}:{user_id}"


class DeparturesRepository:
    def _col(self):
        return mongo_manager.get_global_collection(COLLECTION)

    @translates_db_errors
    async def ensure_indexes(self) -> None:
        await self._col().create_index([("purge_at", pymongo.ASCENDING)], name="purge_at_idx")

    @translates_db_errors
    async def schedule(self, guild_id: str, user_id: str, left_at: datetime) -> None:
        """Forget the member there :data:`PURGE_DELAY` after *left_at* (a first departure wins)."""
        await self._col().update_one(
            {"_id": _key(guild_id, user_id)},
            {
                "$setOnInsert": {
                    "guild_id": str(guild_id),
                    "user_id": str(user_id),
                    "left_at": left_at,
                    "purge_at": left_at + PURGE_DELAY,
                }
            },
            upsert=True,
        )

    @translates_db_errors
    async def cancel(self, guild_id: str, user_id: str) -> bool:
        result = await self._col().delete_one({"_id": _key(guild_id, user_id)})
        return bool(result.deleted_count)

    @translates_db_errors
    async def cancel_guild(self, guild_id: str) -> int:
        """Drop every pending departure of a guild (its whole database is going)."""
        result = await self._col().delete_many({"guild_id": str(guild_id)})
        return int(result.deleted_count)

    @translates_db_errors
    async def due(self, now: datetime) -> list[tuple[str, str]]:
        """``(guild_id, user_id)`` of the departures to act on now."""
        cursor = self._col().find({"purge_at": {"$lte": now}}, {"guild_id": 1, "user_id": 1})
        return [(str(doc["guild_id"]), str(doc["user_id"])) async for doc in cursor]

    @translates_db_errors
    async def done(self, guild_id: str, user_id: str) -> None:
        await self._col().delete_one({"_id": _key(guild_id, user_id)})


__all__ = ["COLLECTION", "PURGE_DELAY", "DeparturesRepository"]
