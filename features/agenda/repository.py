"""What the agenda reads in the guild's database (P19, A5).

Each read goes through the collection a module keeps, bounded by the agenda's
window, and only where the caller says the module is on: the giveaways that
end, the polls that close, the members' birth dates (no year kept here), the
Secret Santa session's exchange day. Nothing is stored for the agenda itself.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from features.gazette.repository import GazetteRepository
from features.secretsanta import SecretSantaRepository
from src.core.db import mongo_manager, translates_db_errors


class AgendaRepository:
    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self, name: str):
        return mongo_manager.get_guild_collection(self._guild_id, name)

    @translates_db_errors
    async def giveaways_ending(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """The giveaways still open that end in the window."""
        cursor = self._col("giveaways").find(
            {"drawn": False, "cancelled": False, "ends_at": {"$gte": start, "$lt": end}},
            {"prize": 1, "ends_at": 1, "channel_id": 1, "message_id": 1},
        )
        return await cursor.to_list()

    @translates_db_errors
    async def polls_closing(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """The polls still open that close in the window."""
        cursor = self._col("polls").find(
            {"closed": False, "closes_at": {"$gte": start, "$lt": end}},
            {"question": 1, "closes_at": 1, "channel_id": 1, "message_id": 1},
        )
        return await cursor.to_list()

    async def birth_dates(self) -> list[tuple[int, date]]:
        """Every member's birth date: ``(member, date)``."""
        return await GazetteRepository(self._guild_id).birth_dates()

    async def santa_exchange(self) -> date | None:
        """The day of the server's Secret Santa exchange, if one is set."""
        session = await SecretSantaRepository().get_session(f"guild_{self._guild_id}")
        return session.exchange_day if session else None


__all__ = ["AgendaRepository"]
