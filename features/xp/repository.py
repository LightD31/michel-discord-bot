"""MongoDB-backed persistence for XP stats and per-message XP events."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pymongo

from src.core.db import mongo_manager


@dataclass
class UserXpStats:
    user_id: str
    xp: int
    msg: int
    lvl: int
    time: float

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> "UserXpStats":
        return cls(
            user_id=doc["_id"],
            xp=doc.get("xp", 0),
            msg=doc.get("msg", 0),
            lvl=doc.get("lvl", 0),
            time=doc.get("time", 0.0),
        )


class XpRepository:
    """Per-guild accessor for the ``xp`` and ``xp_events`` collections."""

    def __init__(self, guild_id: str):
        self.guild_id = guild_id

    def _xp(self):
        return mongo_manager.get_guild_collection(self.guild_id, "xp")

    def _events(self):
        return mongo_manager.get_guild_collection(self.guild_id, "xp_events")

    async def ensure_indexes(self) -> None:
        await self._xp().create_index([("xp", pymongo.DESCENDING)], background=True)
        await self._xp().create_index([("time", pymongo.DESCENDING)], background=True)
        await self._events().create_index(
            [("user_id", pymongo.ASCENDING), ("ts", pymongo.ASCENDING)], background=True
        )

    async def ensure_collection(self, guild_name: str | None = None) -> bool:
        """Create the xp collection for this guild if missing. Returns ``True`` if it was created."""
        guild_db = mongo_manager.get_guild_db(self.guild_id)
        existing = await guild_db.list_collection_names()
        if "xp" in existing:
            return False
        await guild_db.create_collection("xp")
        await self.ensure_indexes()
        return True

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        return await self._xp().find_one({"_id": user_id})

    async def insert_new_user(self, user_id: str, initial_xp: int, timestamp: float) -> None:
        await self._xp().insert_one(
            {"_id": user_id, "xp": initial_xp, "time": timestamp, "msg": 1, "lvl": 0}
        )

    async def update_xp(self, user_id: str, new_xp: int, new_msg: int, timestamp: float) -> None:
        await self._xp().update_one(
            {"_id": user_id},
            {"$set": {"xp": new_xp, "time": timestamp, "msg": new_msg}},
        )

    async def set_level(self, user_id: str, level: int) -> None:
        await self._xp().update_one({"_id": user_id}, {"$set": {"lvl": level}}, upsert=True)

    async def log_event(self, user_id: str, xp_gained: int, total_xp: int, ts: datetime) -> None:
        await self._events().insert_one(
            {"user_id": user_id, "xp_gained": xp_gained, "total_xp": total_xp, "ts": ts}
        )

    async def get_user_rank(self, user_id: str) -> int | None:
        """Return the 1-indexed rank of ``user_id`` ordered by XP desc. ``None`` if absent."""
        pipeline = [
            {"$setWindowFields": {"sortBy": {"xp": -1}, "output": {"rank": {"$rank": {}}}}},
            {"$match": {"_id": user_id}},
            {"$project": {"rank": 1}},
        ]
        try:
            result = await self._xp().aggregate(pipeline).to_list(length=None)
        except pymongo.errors.PyMongoError:
            return await self._get_user_rank_fallback(user_id)
        if result:
            return result[0]["rank"]
        return None

    async def _get_user_rank_fallback(self, user_id: str) -> int | None:
        """Linear-scan fallback when ``$setWindowFields`` isn't available."""
        rankings = self._xp().find({}, {"_id": 1}).sort("xp", -1)
        rank = 0
        async for entry in rankings:
            rank += 1
            if entry["_id"] == user_id:
                return rank
        return None

    async def list_all_sorted_by_xp(self) -> list[dict[str, Any]]:
        cursor = self._xp().find().sort("xp", -1)
        return await cursor.to_list(length=None)


__all__ = ["UserXpStats", "XpRepository"]
