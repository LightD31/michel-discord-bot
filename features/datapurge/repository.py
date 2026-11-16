"""Pending purges of a guild's data, one document per guild in ``global.guild_purges``.

The bot keeps a guild's database (``guild_<id>``) and its settings while it is
in the guild. Once it leaves (kicked, or the guild deleted), they are deleted
:data:`PURGE_DELAY` later, which leaves the time to add the bot back by
mistake or on purpose: a guild seen again cancels its purge.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.core.db import mongo_manager, translates_db_errors

COLLECTION = "guild_purges"
PURGE_DELAY = timedelta(days=30)


class GuildPurgeRepository:
    def _col(self):
        return mongo_manager.get_global_collection(COLLECTION)

    @translates_db_errors
    async def schedule(self, guild_id: str, left_at: datetime) -> None:
        """Purge *guild_id* :data:`PURGE_DELAY` after *left_at*, unless already scheduled."""
        await self._col().update_one(
            {"_id": str(guild_id)},
            {"$setOnInsert": {"left_at": left_at, "purge_at": left_at + PURGE_DELAY}},
            upsert=True,
        )

    @translates_db_errors
    async def cancel(self, guild_ids: list[str]) -> int:
        """Forget the purges of guilds the bot is in again; how many there were."""
        if not guild_ids:
            return 0
        result = await self._col().delete_many({"_id": {"$in": [str(g) for g in guild_ids]}})
        return int(result.deleted_count)

    @translates_db_errors
    async def due(self, now: datetime) -> list[str]:
        """The guilds whose data is to be deleted now."""
        cursor = self._col().find({"purge_at": {"$lte": now}}, {"_id": 1})
        return [str(doc["_id"]) async for doc in cursor]

    @translates_db_errors
    async def scheduled(self) -> dict[str, datetime]:
        """Every pending purge: guild id → when."""
        return {str(doc["_id"]): doc["purge_at"] async for doc in self._col().find({})}

    @translates_db_errors
    async def purge(self, guild_id: str) -> None:
        """Delete the guild's database, then its pending purge."""
        await mongo_manager.drop_guild_database(guild_id)
        await self._col().delete_one({"_id": str(guild_id)})


__all__ = ["COLLECTION", "PURGE_DELAY", "GuildPurgeRepository"]
