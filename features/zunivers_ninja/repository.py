"""MongoDB persistence for the last ZUnivers Ninja plan posted per pseudo.

Public methods raise :class:`src.core.errors.DatabaseError` instead of driver
exceptions, so callers in ``extensions/`` never need to import ``pymongo``.
"""

from datetime import UTC, datetime

from src.core.db import mongo_manager, translates_db_errors


class NinjaRepository:
    """Per-guild accessor for the ``zunivers_ninja`` collection (one document per pseudo)."""

    def __init__(self, guild_id: str):
        self.guild_id = guild_id

    def _col(self):
        return mongo_manager.get_guild_collection(self.guild_id, "zunivers_ninja")

    @staticmethod
    def _key(pseudo: str) -> str:
        return pseudo.strip().lower()

    @translates_db_errors
    async def get_etag(self, pseudo: str) -> str | None:
        """Return the ETag of the last plan posted for *pseudo*, if any."""
        doc = await self._col().find_one({"_id": self._key(pseudo)})
        etag = doc.get("etag") if doc else None
        return etag if isinstance(etag, str) else None

    @translates_db_errors
    async def save_etag(self, pseudo: str, etag: str, plan_hash: str) -> None:
        """Remember that the plan identified by *etag* was handled for *pseudo*."""
        await self._col().update_one(
            {"_id": self._key(pseudo)},
            {
                "$set": {
                    "pseudo": pseudo.strip(),
                    "etag": etag,
                    "hash": plan_hash,
                    "updated_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )
