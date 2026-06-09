"""Olympics repository — persistence for the medal-notification state."""

from src.core.db import mongo_manager


class OlympicsStateRepository:
    """Known-medal state shared across guilds (single global document)."""

    def _col(self):
        return mongo_manager.get_global_collection("olympics_state")

    async def load_known_medals(self) -> set[str] | None:
        """Return the set of already-notified medal keys, or None on first run."""
        doc = await self._col().find_one({"_id": "known_medals"})
        if doc is None:
            return None
        return set(doc.get("medals", []))

    async def save_known_medals(self, medals: set[str]) -> None:
        await self._col().update_one(
            {"_id": "known_medals"},
            {"$set": {"medals": list(medals)}},
            upsert=True,
        )
