"""MongoDB I/O for RSS feed bookkeeping — one document per ``feed_id`` per guild."""

from features.rss.models import RssFeedState
from src.core import clock
from src.core.db import mongo_manager, translates_db_errors

COLLECTION = "rss"
# How many seen entry ids to keep per feed. Larger = more dedupe history but
# more wasted disk; 200 covers a few weeks for typical news feeds.
MAX_SEEN_IDS = 200


class RssRepository:
    """Per-guild store for RSS feed state."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    @translates_db_errors
    async def get(self, feed_id: str) -> RssFeedState | None:
        doc = await self._col().find_one({"_id": feed_id})
        return RssFeedState(**doc) if doc else None

    @translates_db_errors
    async def states(self) -> dict[str, RssFeedState]:
        """Every feed state of the guild, by feed id: one read per poll cycle."""
        states: dict[str, RssFeedState] = {}
        async for doc in self._col().find({}):
            state = RssFeedState(**doc)
            states[state.feed_id] = state
        return states

    @translates_db_errors
    async def initialize(self, feed_id: str, seen_ids: list[str]) -> None:
        """Mark a feed as initialized — seed dedupe set without posting backlog."""
        await self._col().update_one(
            {"_id": feed_id},
            {
                "$set": {
                    "seen_ids": seen_ids[:MAX_SEEN_IDS],
                    "initialized": True,
                    "last_poll_at": clock.now(),
                    "last_error": None,
                    "failures": 0,
                }
            },
            upsert=True,
        )

    @translates_db_errors
    async def record_poll(self, feed_id: str, new_ids: list[str]) -> None:
        """Stamp a successful poll and prepend *new_ids* to the dedupe set.

        Called after every successful fetch, new entries or not: the poll time
        is what spaces the next fetch out, and it clears the failure streak.
        ``$push`` with ``$position: 0`` + ``$slice`` keeps the set capped at
        ``MAX_SEEN_IDS`` in the same atomic update.
        """
        update: dict = {"$set": {"last_poll_at": clock.now(), "last_error": None, "failures": 0}}
        if new_ids:
            update["$push"] = {
                "seen_ids": {"$each": new_ids, "$position": 0, "$slice": MAX_SEEN_IDS}
            }
        await self._col().update_one({"_id": feed_id}, update, upsert=True)

    @translates_db_errors
    async def record_error(self, feed_id: str, message: str) -> None:
        """Stamp a failed poll: one more consecutive failure, a longer wait."""
        await self._col().update_one(
            {"_id": feed_id},
            {
                "$set": {"last_poll_at": clock.now(), "last_error": message},
                "$inc": {"failures": 1},
            },
            upsert=True,
        )

    @translates_db_errors
    async def delete(self, feed_id: str) -> int:
        result = await self._col().delete_one({"_id": feed_id})
        return result.deleted_count
