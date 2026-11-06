"""Daily counters of AI model calls, in the shared ``global`` database.

One document per (UTC day, user) and one per day for everyone, removed by a
TTL index once the day is over (see :func:`features.ai.quota.day_expiry`).
Public methods raise :class:`src.core.errors.DatabaseError`, never driver
exceptions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ReturnDocument

from features.ai.quota import GLOBAL, USER, AiLimits, day_expiry, day_key, over_limit
from src.core.db import mongo_manager, translates_db_errors

COLLECTION = "ai_usage"


class AiUsageRepository:
    def _col(self) -> Any:
        return mongo_manager.get_global_collection(COLLECTION)

    @translates_db_errors
    async def ensure_indexes(self) -> None:
        await self._col().create_index("expires_at", expireAfterSeconds=0, name="expires_ttl")

    async def _add(self, key: str, calls: int, now: datetime) -> int:
        doc = await self._col().find_one_and_update(
            {"_id": key},
            {
                "$inc": {"count": calls},
                "$setOnInsert": {"day": day_key(now), "expires_at": day_expiry(now)},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["count"])

    @translates_db_errors
    async def reserve(
        self, user_id: str, calls: int, limits: AiLimits, now: datetime
    ) -> str | None:
        """Count *calls* for *user_id* today, unless that crosses a limit.

        Returns ``None`` when reserved, else :data:`~features.ai.quota.USER` or
        :data:`~features.ai.quota.GLOBAL` — and then nothing is counted. The
        increments are atomic, so concurrent questions can't both slip under
        a limit.
        """
        day = day_key(now)
        user_key = f"{day}:user:{user_id}"
        global_key = f"{day}:all"
        if over_limit(await self._add(user_key, calls, now), limits.user_daily_calls):
            await self._add(user_key, -calls, now)
            return USER
        if over_limit(await self._add(global_key, calls, now), limits.daily_calls):
            await self._add(global_key, -calls, now)
            await self._add(user_key, -calls, now)
            return GLOBAL
        return None


__all__ = ["COLLECTION", "AiUsageRepository"]
