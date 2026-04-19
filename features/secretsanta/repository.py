"""MongoDB I/O for the Secret Santa feature.

Guild contexts use per-guild databases; DM / group-DM contexts fall back to the
global database.
"""

import os
from dataclasses import asdict
from datetime import datetime

from src.core.db import mongo_manager
from src.core.logging import init_logger

from .models import SecretSantaSession

logger = init_logger(os.path.basename(__file__))


class SecretSantaRepository:
    """All persistence for sessions, banned pairs and draw results."""

    def _collections(self, context_id: str):
        """Return ``(sessions, draw_results, banned_pairs)`` collections for ``context_id``."""
        if context_id.startswith("guild_"):
            guild_id = context_id.removeprefix("guild_")
            db = mongo_manager.get_guild_db(guild_id)
        else:
            db = mongo_manager.global_db
        return (
            db["secret_santa_sessions"],
            db["secret_santa_draw_results"],
            db["secret_santa_banned_pairs"],
        )

    async def get_session(self, context_id: str) -> SecretSantaSession | None:
        sessions_col, _, _ = self._collections(context_id)
        doc = await sessions_col.find_one({"_id": context_id})
        if not doc:
            return None
        doc["context_id"] = doc.pop("_id")
        return SecretSantaSession(**doc)

    async def save_session(self, session: SecretSantaSession) -> None:
        sessions_col, _, _ = self._collections(session.context_id)
        data = asdict(session)
        data["_id"] = data.pop("context_id")
        await sessions_col.update_one({"_id": data["_id"]}, {"$set": data}, upsert=True)
        logger.info(f"Session saved for {session.context_id}")

    async def delete_session(self, context_id: str) -> bool:
        sessions_col, _, _ = self._collections(context_id)
        result = await sessions_col.delete_one({"_id": context_id})
        if result.deleted_count > 0:
            logger.info(f"Session deleted for {context_id}")
            return True
        return False

    async def read_banned_pairs(self, context_id: str) -> list[tuple[int, int]]:
        _, _, banned_pairs_col = self._collections(context_id)
        doc = await banned_pairs_col.find_one({"_id": context_id})
        if doc:
            return [tuple(p) for p in doc.get("pairs", [])]
        return []

    async def write_banned_pairs(
        self, context_id: str, banned_pairs: list[tuple[int, int]]
    ) -> None:
        _, _, banned_pairs_col = self._collections(context_id)
        await banned_pairs_col.update_one(
            {"_id": context_id},
            {"$set": {"pairs": [list(p) for p in banned_pairs]}},
            upsert=True,
        )
        logger.info(f"Banned pairs updated for {context_id}")

    async def save_draw_results(self, context_id: str, draw_results: list[tuple[int, int]]) -> None:
        _, draw_results_col, _ = self._collections(context_id)
        await draw_results_col.update_one(
            {"_id": context_id},
            {
                "$set": {
                    "results": [list(p) for p in draw_results],
                    "drawn_at": datetime.now().isoformat(),
                }
            },
            upsert=True,
        )
        logger.info(f"Draw results saved for {context_id}")

    async def get_draw_results(self, context_id: str) -> list[tuple[int, int]] | None:
        _, draw_results_col, _ = self._collections(context_id)
        doc = await draw_results_col.find_one({"_id": context_id})
        if doc:
            return [tuple(p) for p in doc.get("results", [])]
        return None
