"""MDI repository — per-match message metadata persisted between restarts."""

from typing import Any

from src.core.db import mongo_manager


class MdiMatchesRepository:
    def __init__(self, guild_id) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "mdi_matches")

    async def load_all(self) -> list[dict[str, Any]]:
        return await self._col().find({}).to_list(length=None)

    async def upsert(self, doc: dict[str, Any]) -> None:
        await self._col().replace_one({"_id": doc["_id"]}, doc, upsert=True)
