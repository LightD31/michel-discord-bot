"""Zevent repository — persistence for the announcement markers.

Kept per guild and per edition: each server announces into its own channel,
and an edition opens back at zero, so last year's markers must never gate this
year's announcements.
"""

from src.core.db import GuildRepository, translates_db_errors

COLLECTION = "zevent_state"
# Document id for an edition the stats API could not identify. Guessing an
# edition would be worse than sharing one marker across those runs.
UNKNOWN_EDITION = "unknown"


class ZeventStateRepository(GuildRepository):
    """Stores the highest donation milestone and the record marker."""

    COLLECTION = COLLECTION

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

    @translates_db_errors
    async def load_record(self, event_id: str | None) -> bool | None:
        """Whether this edition's record announcement has already gone out.

        ``None`` means this edition has never been read: the tracker then has
        no way to tell a record that fell on its watch from one that was
        already behind it when it started, and stays quiet.
        """
        doc = await self._col().find_one({"_id": self._doc_id(event_id)})
        if not doc:
            return None
        value = doc.get("record_announced")
        return value if isinstance(value, bool) else None

    @translates_db_errors
    async def save_record(self, event_id: str | None, announced: bool) -> None:
        """Persist the record marker; ``False`` records "seen below it"."""
        await self._col().update_one(
            {"_id": self._doc_id(event_id)},
            {"$set": {"record_announced": bool(announced)}},
            upsert=True,
        )
