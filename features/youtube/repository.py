"""YouTube repository — persistence for the last-notified video per channel.

One document per guild holds the list of ``{"handle", "video_id"}`` pairs. The
handles are values, not field names: a handle may contain a dot (``@john.doe``),
which MongoDB would read as a nested path in an update, so the video would never
be found again and would be re-posted on every poll.
"""

from src.core.db import mongo_manager, translates_db_errors

DOCUMENT_ID = "last_videos"


class YoutubeRepository:
    def __init__(self, guild_id) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "youtube")

    @translates_db_errors
    async def get_last_video_ids(self) -> dict[str, str] | None:
        """Return the handle → last-video-id map, or None if never synced."""
        doc = await self._col().find_one({"_id": DOCUMENT_ID})
        if doc is None:
            return None
        return {entry["handle"]: entry["video_id"] for entry in doc["channels"]}

    @translates_db_errors
    async def save_last_video_ids(self, ids: dict[str, str]) -> None:
        """Replace the stored map: channels no longer followed are dropped."""
        channels = [{"handle": handle, "video_id": video} for handle, video in ids.items()]
        await self._col().replace_one(
            {"_id": DOCUMENT_ID}, {"_id": DOCUMENT_ID, "channels": channels}, upsert=True
        )
