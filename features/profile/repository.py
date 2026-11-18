"""What ``/profil`` reads: counts the server already shows publicly, one module at a time.

Nothing is stored for the profile itself. Each count reads the collection the
module keeps (the starboard's mirrors, the playlist, suggestions, giveaways,
birthdays) and is only asked for when that module is on: the caller says which
(``sections``), so a module switched off shows nothing even if its data stays.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.core.db import mongo_manager, translates_db_errors

SECTIONS = frozenset({"stars", "tracks", "suggestions", "giveaways", "birthday"})


@dataclass
class Footprint:
    """``None``: the module is off here (the line isn't shown); 0: on, nothing yet."""

    stars: int | None = None  # messages of theirs on the starboard
    stars_received: int | None = None  # the stars those messages gathered
    tracks: int | None = None  # tracks they added to the playlist
    suggestions_done: int | None = None  # their suggestions approved or implemented
    giveaways_won: int | None = None
    birthday: datetime | None = None  # a calendar day, only when the year may show
    birthday_hides_year: bool = False


class ProfileRepository:
    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self, name: str):
        return mongo_manager.get_guild_collection(self._guild_id, name)

    @translates_db_errors
    async def footprint(self, user_id: str, sections: frozenset[str] | set[str]) -> Footprint:
        uid = str(user_id)
        found = Footprint()
        if "stars" in sections:
            docs = await self._col("starboard").find({"author_id": uid}, {"count": 1}).to_list()
            found.stars = len(docs)
            found.stars_received = sum(int(d.get("count", 0)) for d in docs)
        if "tracks" in sections:
            found.tracks = await self._col("playlistItemsFull").count_documents({"added_by": uid})
        if "suggestions" in sections:
            found.suggestions_done = await self._col("suggestions").count_documents(
                {"author_id": uid, "status": {"$in": ["approved", "implemented"]}}
            )
        if "giveaways" in sections:
            found.giveaways_won = await self._col("giveaways").count_documents({"winners": uid})
        if "birthday" in sections and uid.isdigit():
            doc = await self._col("birthday").find_one({"user": int(uid)})
            if doc and doc.get("date"):
                found.birthday = doc["date"]
                found.birthday_hides_year = bool(doc.get("hideyear"))
        return found


__all__ = ["SECTIONS", "Footprint", "ProfileRepository"]
