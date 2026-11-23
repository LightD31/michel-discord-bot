"""VLR.gg repositories — live-match messages and sent reminders, persisted between restarts."""

from datetime import UTC, datetime
from typing import Any

from src.core.db import GuildRepository, translates_db_errors


class VlrggLiveRepository(GuildRepository):
    COLLECTION = "vlrgg_live"

    @translates_db_errors
    async def load_all(self) -> list[dict[str, Any]]:
        return await self._col().find({}).to_list()

    @translates_db_errors
    async def upsert(self, doc: dict[str, Any]) -> None:
        await self._col().replace_one({"_id": doc["_id"]}, doc, upsert=True)

    @translates_db_errors
    async def delete(self, match_id: str) -> None:
        await self._col().delete_one({"_id": match_id})


class VlrggReminderRepository(GuildRepository):
    """Matchs pour lesquels le rappel d'avant-match a déjà été envoyé (un par serveur)."""

    COLLECTION = "vlrgg_reminders"

    @translates_db_errors
    async def was_sent(self, match_id: str) -> bool:
        return await self._col().find_one({"_id": match_id}) is not None

    @translates_db_errors
    async def mark_sent(self, match_id: str, team_name: str) -> None:
        await self._col().replace_one(
            {"_id": match_id},
            {"_id": match_id, "team_name": team_name, "sent_at": datetime.now(UTC)},
            upsert=True,
        )
