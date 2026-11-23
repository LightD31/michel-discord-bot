"""What the Gazette reads (each module's own collection, one week) and its editions.

Every read is bounded: one week of starboard mirrors, suggestions and
giveaways, the playlist's dates (projected), the birthdays of the server.
``gazette_editions`` holds one document per edition (``_id`` the ISO week),
claimed before the post so the weekly task posts once.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

import pymongo
from pymongo.errors import DuplicateKeyError

from features.spotify.stats import parse_timestamp
from src.core.db import GuildRepository, translates_db_errors

EDITIONS = "gazette_editions"


class GazetteRepository(GuildRepository):
    # --- The week's news -----------------------------------------------------

    @translates_db_errors
    async def best_star(self, start: datetime, end: datetime) -> dict[str, Any] | None:
        """The starboard mirror of the week with the most stars."""
        return await self._col("starboard").find_one(
            {"created_at": {"$gte": start, "$lt": end}},
            sort=[("count", pymongo.DESCENDING), ("created_at", pymongo.ASCENDING)],
        )

    @translates_db_errors
    async def birth_dates(self) -> list[tuple[int, date]]:
        """Every member's birth date (a calendar day): ``(member, date)``."""
        docs = await self._col("birthday").find({}, {"user": 1, "date": 1}).to_list()
        return [(int(d["user"]), d["date"].date()) for d in docs if d.get("date")]

    @translates_db_errors
    async def tracks_added(self, start: datetime, end: datetime) -> Counter[str]:
        """Tracks added in the week, per member (``added_at`` is a string or a date)."""
        docs = (
            await self._col("playlistItemsFull").find({}, {"added_at": 1, "added_by": 1}).to_list()
        )
        counts: Counter[str] = Counter()
        for doc in docs:
            when = parse_timestamp(doc.get("added_at"))
            if when is not None and start <= when < end:
                counts[str(doc.get("added_by") or "")] += 1
        return counts

    @translates_db_errors
    async def decided_suggestions(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return (
            await self._col("suggestions")
            .find(
                {"decided_at": {"$gte": start, "$lt": end}, "status": {"$ne": "pending"}},
                {"id": 1, "status": 1, "text": 1, "title": 1},
            )
            .sort("decided_at", pymongo.ASCENDING)
            .to_list()
        )

    @translates_db_errors
    async def drawn_giveaways(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return (
            await self._col("giveaways")
            .find(
                {"ends_at": {"$gte": start, "$lt": end}, "drawn": True, "cancelled": {"$ne": True}},
                {"prize": 1, "winners": 1},
            )
            .sort("ends_at", pymongo.ASCENDING)
            .to_list()
        )

    # --- Editions ------------------------------------------------------------

    @translates_db_errors
    async def claim_edition(self, key: str, now: datetime) -> bool:
        """Take the week's edition; ``False`` when it was already taken (or posted)."""
        try:
            await self._col(EDITIONS).insert_one({"_id": key, "claimed_at": now})
        except DuplicateKeyError:
            return False
        return True

    @translates_db_errors
    async def release_edition(self, key: str) -> None:
        """Give a claimed edition back when its post failed, so the next run tries again."""
        await self._col(EDITIONS).delete_one({"_id": key, "message_id": {"$exists": False}})

    @translates_db_errors
    async def set_edition_message(self, key: str, channel_id: str, message_id: str) -> None:
        await self._col(EDITIONS).update_one(
            {"_id": key}, {"$set": {"channel_id": channel_id, "message_id": message_id}}
        )

    @translates_db_errors
    async def last_edition(self) -> dict[str, Any] | None:
        return await self._col(EDITIONS).find_one(
            {"message_id": {"$exists": True}}, sort=[("claimed_at", pymongo.DESCENDING)]
        )


__all__ = ["GazetteRepository"]
