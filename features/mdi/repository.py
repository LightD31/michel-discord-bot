"""MDI repository — per-match message metadata persisted between restarts."""

from typing import Any

from src.core.db import GuildRepository, translates_db_errors


class MdiMatchesRepository(GuildRepository):
    COLLECTION = "mdi_matches"

    @translates_db_errors
    async def load_all(self) -> list[dict[str, Any]]:
        return await self._col().find({}).to_list()

    @translates_db_errors
    async def upsert(self, doc: dict[str, Any]) -> None:
        await self._col().replace_one({"_id": doc["_id"]}, doc, upsert=True)
