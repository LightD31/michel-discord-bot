"""Twitch repository — persistence for per-streamer session state.

State is shared across guilds (a streamer tracked by several servers has a
single document), hence the global collection.
"""

from typing import Any

from src.core.db import mongo_manager


class TwitchStateRepository:
    def _col(self):
        return mongo_manager.get_global_collection("twitch_streamer_state")

    async def load_all(self) -> dict[str, dict[str, Any]]:
        """Return every streamer-state document, keyed by streamer id."""
        states: dict[str, dict[str, Any]] = {}
        async for doc in self._col().find():
            states[doc["_id"]] = doc
        return states

    async def save(self, streamer_id: str, fields: dict[str, Any]) -> None:
        await self._col().update_one({"_id": streamer_id}, {"$set": fields}, upsert=True)


class TwitchEmotesRepository:
    """Cached emote set for one streamer (one global collection per streamer).

    Document schema: ``{_id: emote_id, name: str, cached_file: str | None}``.
    """

    def __init__(self, streamer_id: str) -> None:
        self._streamer_id = streamer_id

    def _col(self):
        return mongo_manager.get_global_collection(f"twitch_emotes_{self._streamer_id}")

    async def load_all(self) -> dict[str, dict[str, Any]]:
        """Return emotes keyed by emote id, as ``{name, cached_file}`` dicts."""
        data: dict[str, dict[str, Any]] = {}
        async for doc in self._col().find():
            data[doc["_id"]] = {
                "name": doc.get("name", ""),
                "cached_file": doc.get("cached_file"),
            }
        return data

    async def insert_many(self, docs: list[dict[str, Any]]) -> None:
        await self._col().insert_many(docs)

    async def replace_all(self, docs: list[dict[str, Any]]) -> None:
        await self._col().delete_many({})
        if docs:
            await self._col().insert_many(docs)
