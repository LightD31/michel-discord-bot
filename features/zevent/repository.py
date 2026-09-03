"""Zevent repository — persistence for the donation-milestone marker.

Kept per guild and per edition: each server announces into its own channel,
and an edition opens back at zero, so last year's marker must never gate this
year's announcements.
"""

from src.core.db import mongo_manager, translates_db_errors

COLLECTION = "zevent_state"
# Document id for an edition the stats API could not identify. Guessing an
# edition would be worse than sharing one marker across those runs.
UNKNOWN_EDITION = "unknown"


class ZeventStateRepository:
    """Stores the highest donation milestone already announced."""

    def __init__(self, guild_id: str | int) -> None:
        self.guild_id = guild_id

    def _col(self):
        return mongo_manager.get_guild_collection(self.guild_id, COLLECTION)

    @staticmethod
    def _doc_id(event_id: str | None) -> str:
        return event_id or UNKNOWN_EDITION

    @translates_db_errors
    async def load_milestone(self, event_id: str | None) -> int | None:
        """Return the last milestone announced for this edition, if any."""
        doc = await self._col().find_one({"_id": self._doc_id(event_id)})
        if not doc:
            return None
        value = doc.get("last_milestone")
        return int(value) if isinstance(value, int | float) else None

    @translates_db_errors
    async def save_milestone(self, event_id: str | None, milestone: int) -> None:
        await self._col().update_one(
            {"_id": self._doc_id(event_id)},
            {"$set": {"last_milestone": int(milestone)}},
            upsert=True,
        )
