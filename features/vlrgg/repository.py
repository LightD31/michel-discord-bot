"""VLR.gg repository — live-match message state persisted between restarts."""

from typing import Any

from src.core.db import mongo_manager, translates_db_errors


class VlrggLiveRepository:
    def __init__(self, guild_id) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "vlrgg_live")

    @translates_db_errors
    async def load_all(self) -> list[dict[str, Any]]:
        return await self._col().find({}).to_list(length=None)

    @translates_db_errors
    async def upsert(self, doc: dict[str, Any]) -> None:
        await self._col().replace_one({"_id": doc["_id"]}, doc, upsert=True)

    @translates_db_errors
    async def delete(self, match_id: str) -> None:
        await self._col().delete_one({"_id": match_id})
