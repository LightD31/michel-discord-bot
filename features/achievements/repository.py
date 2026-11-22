"""Who won which badge (``achievements``), and who qualifies for one (P18, A3).

``achievements`` holds one document per badge won: ``_id`` ``"<user>:<badge>"``,
``user_id``, ``badge`` and ``earned_at``. A badge is never taken back: the
data it was read from may go (a playlist emptied, XP events folded after 400
days), the badge stays. Awarding is an upsert per member that only inserts, so
two runs racing award a badge once, and the run tells which awards were new.

``achievements_state`` remembers, per badge, that the server's existing
members were already looked at: the first check of a badge awards in silence
(otherwise switching the module on would announce the whole server).

The ``qualifying_*`` reads go through the collections the other modules keep,
as :mod:`features.profile` does; anonymised entries (:data:`NOBODY`) qualify
nobody.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

import pymongo

from features.achievements.badges import DJ_TRACKS, FEUR_COUNT, VOICE_TICKS
from src.core.db import mongo_manager, translates_db_errors

ACHIEVEMENTS = "achievements"
STATE = "achievements_state"


def _member_ids(values: Iterable[Any]) -> set[str]:
    """The real member ids among *values*: digits, not an anonymised ``0…``."""
    ids = set()
    for value in values:
        text = str(value or "")
        if text.isdigit() and not text.startswith("0"):
            ids.add(text)
    return ids


class AchievementsRepository:
    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self, name: str = ACHIEVEMENTS):
        return mongo_manager.get_guild_collection(self._guild_id, name)

    @translates_db_errors
    async def ensure_indexes(self) -> None:
        await self._col().create_index([("user_id", pymongo.ASCENDING)], name="user_id")

    # --- Badges won ------------------------------------------------------------

    @translates_db_errors
    async def award(self, badge: str, user_ids: Iterable[str], at: datetime) -> list[str]:
        """Give *badge* to each of *user_ids*; the ids that didn't have it yet."""
        ids = sorted(_member_ids(user_ids))
        if not ids:
            return []
        ops = [
            pymongo.UpdateOne(
                {"_id": f"{uid}:{badge}"},
                {"$setOnInsert": {"user_id": uid, "badge": badge, "earned_at": at}},
                upsert=True,
            )
            for uid in ids
        ]
        result = await self._col().bulk_write(ops, ordered=False)
        return [ids[index] for index in sorted(result.upserted_ids)]

    @translates_db_errors
    async def badges_of(self, user_id: str) -> dict[str, datetime]:
        """The member's badges: key -> when it was won."""
        cursor = self._col().find({"user_id": str(user_id)}, {"badge": 1, "earned_at": 1})
        return {doc["badge"]: doc["earned_at"] async for doc in cursor}

    @translates_db_errors
    async def badges_of_many(self, user_ids: Iterable[str]) -> dict[str, list[str]]:
        """Each member's badge keys, for a leaderboard."""
        ids = sorted(_member_ids(user_ids))
        found: dict[str, list[str]] = {uid: [] for uid in ids}
        if not ids:
            return found
        cursor = self._col().find({"user_id": {"$in": ids}}, {"user_id": 1, "badge": 1})
        async for doc in cursor:
            found[doc["user_id"]].append(doc["badge"])
        return found

    # --- First check of a badge -----------------------------------------------

    @translates_db_errors
    async def seeded(self) -> set[str]:
        """The badges whose existing holders were already awarded in silence."""
        return {doc["_id"] async for doc in self._col(STATE).find({}, {"_id": 1})}

    @translates_db_errors
    async def mark_seeded(self, badge: str, at: datetime) -> None:
        await self._col(STATE).update_one(
            {"_id": badge}, {"$setOnInsert": {"seeded_at": at}}, upsert=True
        )

    # --- Who qualifies -----------------------------------------------------------

    @translates_db_errors
    async def qualifying_star(self) -> set[str]:
        """Authors of a message on the starboard."""
        return _member_ids(await self._col("starboard").distinct("author_id"))

    @translates_db_errors
    async def qualifying_voice(self) -> set[str]:
        """Members with :data:`VOICE_TICKS` voice ticks in the XP log (its 400 days)."""
        return await self._at_least("xp_events", {"source": "voice"}, "$user_id", VOICE_TICKS)

    @translates_db_errors
    async def qualifying_dj(self) -> set[str]:
        """Members who added :data:`DJ_TRACKS` tracks to the playlist."""
        return await self._at_least("playlistItemsFull", {}, "$added_by", DJ_TRACKS)

    @translates_db_errors
    async def qualifying_ideas(self) -> set[str]:
        """Authors of a suggestion marked implemented."""
        found = await self._col("suggestions").distinct("author_id", {"status": "implemented"})
        return _member_ids(found)

    @translates_db_errors
    async def qualifying_feur(self) -> set[str]:
        """Members whose feur counter reached :data:`FEUR_COUNT`."""
        cursor = self._col("feur_stats").find(
            {"_id": {"$regex": "^user_"}, "total": {"$gte": FEUR_COUNT}}, {"_id": 1}
        )
        return _member_ids([doc["_id"].removeprefix("user_") async for doc in cursor])

    async def _at_least(
        self, collection: str, match: dict[str, Any], field: str, count: int
    ) -> set[str]:
        pipeline: list[dict[str, Any]] = [
            {"$match": match},
            {"$group": {"_id": field, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gte": count}}},
        ]
        rows = await (await self._col(collection).aggregate(pipeline)).to_list()
        return _member_ids(row["_id"] for row in rows)


__all__ = ["ACHIEVEMENTS", "STATE", "AchievementsRepository"]
