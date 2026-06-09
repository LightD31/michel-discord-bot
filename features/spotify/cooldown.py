"""Mongo-backed per-user vote cooldown for Spotify button interactions.

A tiny TTL-indexed collection stores the last vote time per user. Mongo expires
entries automatically so the collection stays bounded across bot lifetimes.
"""

import os
import time

import pymongo

from src.core.db import mongo_manager
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

COLLECTION = "spotify_vote_cooldowns"


class VoteCooldown:
    """Shared vote cooldown backed by a global TTL-indexed collection."""

    def __init__(self, cooldown_seconds: float) -> None:
        self._cooldown = cooldown_seconds
        self._indexes_ready = False

    def _col(self):
        return mongo_manager.get_global_collection(COLLECTION)

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        try:
            # Docs carry their own monotonic epoch timestamp, but we also set
            # a Date field so Mongo can reap them. TTL grace = 60s, well above
            # any realistic per-user cooldown.
            await self._col().create_index(
                [("expires_at", pymongo.ASCENDING)],
                expireAfterSeconds=0,
                name="expires_at_ttl",
            )
            self._indexes_ready = True
        except Exception as e:
            logger.error("Failed to create vote cooldown TTL index: %s", e)

    async def is_on_cooldown(self, user_id: str) -> bool:
        await self._ensure_indexes()
        try:
            doc = await self._col().find_one({"_id": user_id})
        except Exception as e:
            logger.error("Cooldown lookup failed for %s: %s", user_id, e)
            return False
        if not doc:
            return False
        last = doc.get("last_vote_ts", 0.0)
        return (time.time() - last) < self._cooldown

    async def record(self, user_id: str) -> None:
        await self._ensure_indexes()
        from datetime import datetime, timedelta

        try:
            await self._col().update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "last_vote_ts": time.time(),
                        "expires_at": datetime.utcnow() + timedelta(seconds=self._cooldown),
                    }
                },
                upsert=True,
            )
        except Exception as e:
            logger.error("Failed to record vote cooldown for %s: %s", user_id, e)
