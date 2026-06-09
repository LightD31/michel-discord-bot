"""YouTube repository — persistence for the last-notified video per channel."""

from src.core.db import mongo_manager


class YoutubeRepository:
    def __init__(self, guild_id) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "youtube")

    async def get_last_video_ids(self) -> dict[str, str] | None:
        """Return the handle → last-video-id map, or None if never synced."""
        doc = await self._col().find_one({"_id": "youtube_data"})
        if doc is None:
            return None
        return {k: v for k, v in doc.items() if k != "_id"}

    async def save_last_video_ids(self, ids: dict[str, str]) -> None:
        await self._col().update_one({"_id": "youtube_data"}, {"$set": ids}, upsert=True)
