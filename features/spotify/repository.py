"""MongoDB I/O for the Spotify feature — playlist mirror, votes, and reminders.

One thin async method per persistence operation used by the Spotify
extension. Collections live in each guild's database:

- ``playlistItemsFull`` — mirror of the live Spotify playlist tracks.
- ``votes``             — daily keep/remove poll archive (one doc per track).
- ``vote_infos``        — pointer to the current poll (single ``current`` doc).
- ``snapshot``          — playlist snapshot id / total duration / length.
- ``reminders``         — vote reminder times and subscribed user ids.
- ``addwithvotes``      — pending add-with-vote polls.
"""

from typing import Any

from pymongo import ReturnDocument

from src.core.db import mongo_manager

PLAYLIST_COLLECTION = "playlistItemsFull"
VOTES_COLLECTION = "votes"
VOTE_INFOS_COLLECTION = "vote_infos"
SNAPSHOT_COLLECTION = "snapshot"
REMINDERS_COLLECTION = "reminders"
ADDWITHVOTES_COLLECTION = "addwithvotes"


class SpotifyRepository:
    """Per-guild MongoDB store backing the Spotify extension."""

    def __init__(self, guild_id: str | int) -> None:
        self.guild_id = str(guild_id)

    def _col(self, name: str):
        return mongo_manager.get_guild_collection(self.guild_id, name)

    # --- vote_infos ----------------------------------------------------

    async def get_vote_infos(self) -> dict[str, Any] | None:
        """Return the raw ``current`` vote-infos document, if any."""
        return await self._col(VOTE_INFOS_COLLECTION).find_one({"_id": "current"})

    async def save_vote_infos(self, vote_infos: dict[str, Any]) -> None:
        await self._col(VOTE_INFOS_COLLECTION).update_one(
            {"_id": "current"}, {"$set": vote_infos}, upsert=True
        )

    # --- snapshot --------------------------------------------------------

    async def get_snapshot(self) -> dict[str, Any] | None:
        """Return the raw ``current`` snapshot document, if any."""
        return await self._col(SNAPSHOT_COLLECTION).find_one({"_id": "current"})

    async def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        await self._col(SNAPSHOT_COLLECTION).update_one(
            {"_id": "current"}, {"$set": snapshot}, upsert=True
        )

    # --- reminders -------------------------------------------------------

    async def list_reminders(self) -> list[dict[str, Any]]:
        """Return every reminder document."""
        return [doc async for doc in self._col(REMINDERS_COLLECTION).find()]

    async def replace_reminders(self, docs: list[dict[str, Any]]) -> None:
        """Wipe the reminders collection and insert the given documents."""
        col = self._col(REMINDERS_COLLECTION)
        await col.delete_many({})
        for doc in docs:
            await col.insert_one(doc)

    # --- playlistItemsFull -------------------------------------------------

    async def playlist_track_ids(self) -> list[str]:
        """Return the distinct ``_id`` values of the mirrored playlist."""
        return await self._col(PLAYLIST_COLLECTION).distinct("_id")

    async def add_playlist_item(self, song: dict[str, Any]) -> None:
        await self._col(PLAYLIST_COLLECTION).insert_one(song)

    async def get_playlist_item(self, track_id: str) -> dict[str, Any] | None:
        return await self._col(PLAYLIST_COLLECTION).find_one({"_id": track_id})

    async def pop_playlist_item(self, track_id: str) -> dict[str, Any] | None:
        """Delete a playlist item and return the removed document."""
        return await self._col(PLAYLIST_COLLECTION).find_one_and_delete({"_id": track_id})

    async def delete_playlist_item(self, track_id: str) -> None:
        await self._col(PLAYLIST_COLLECTION).delete_one({"_id": track_id})

    async def find_playlist_items(self, query: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Return playlist documents matching ``query``, keyed by ``_id``."""
        return {item["_id"]: item async for item in self._col(PLAYLIST_COLLECTION).find(query)}

    # --- votes -------------------------------------------------------------

    async def get_votes_doc(self, track_id: str) -> dict[str, Any] | None:
        return await self._col(VOTES_COLLECTION).find_one({"_id": track_id})

    async def voted_track_ids(self) -> list[str]:
        """Return the distinct ``_id`` values of tracks already polled."""
        return await self._col(VOTES_COLLECTION).distinct("_id")

    async def find_vote_docs(self, query: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Return vote documents matching ``query``, keyed by ``_id``."""
        return {item["_id"]: item async for item in self._col(VOTES_COLLECTION).find(query)}

    async def init_vote_doc(self, track_id: str, fields: dict[str, Any]) -> None:
        """Upsert the vote document opened for a new daily poll."""
        await self._col(VOTES_COLLECTION).update_one(
            {"_id": track_id}, {"$set": fields}, upsert=True
        )

    async def set_vote_state(self, track_id: str, state: str) -> dict[str, Any] | None:
        """Mark a closed poll as kept/removed; returns the pre-update document."""
        return await self._col(VOTES_COLLECTION).find_one_and_update(
            {"_id": track_id}, {"$set": {"state": state}}
        )

    async def record_vote(self, track_id: str, user_id: str, vote: str) -> dict[str, Any] | None:
        """Set a user's vote and return the updated document."""
        return await self._col(VOTES_COLLECTION).find_one_and_update(
            {"_id": track_id},
            {"$set": {f"votes.{user_id}": vote}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

    async def remove_vote(self, track_id: str, user_id: str) -> dict[str, Any] | None:
        """Unset a user's vote and return the updated document."""
        return await self._col(VOTES_COLLECTION).find_one_and_update(
            {"_id": track_id},
            {"$unset": {f"votes.{user_id}": ""}},
            return_document=ReturnDocument.AFTER,
        )

    # --- addwithvotes --------------------------------------------------------

    async def load_addwithvote_data(self) -> dict[str, dict[str, Any]]:
        """Return all pending add-with-vote entries keyed by song id (sans ``_id``)."""
        data: dict[str, dict[str, Any]] = {}
        async for doc in self._col(ADDWITHVOTES_COLLECTION).find():
            song_id = doc["_id"]
            data[song_id] = {k: v for k, v in doc.items() if k != "_id"}
        return data

    async def save_addwithvote_data(self, data: dict[str, dict[str, Any]]) -> None:
        """Upsert every add-with-vote entry of ``data``."""
        col = self._col(ADDWITHVOTES_COLLECTION)
        for song_id, song_data in data.items():
            await col.update_one({"_id": song_id}, {"$set": song_data}, upsert=True)

    async def get_addwithvote_doc(self, song_id: str) -> dict[str, Any] | None:
        return await self._col(ADDWITHVOTES_COLLECTION).find_one({"_id": song_id})

    async def save_addwithvote_vote(self, author_id: str, vote: str, song_id: str) -> None:
        await self._col(ADDWITHVOTES_COLLECTION).update_one(
            {"_id": song_id}, {"$set": {f"votes.{author_id}": vote}}
        )
