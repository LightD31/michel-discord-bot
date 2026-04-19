"""MongoDB I/O for the Uptime feature — per-guild maintenance monitor configs."""

import os

from src.core.db import mongo_manager
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))


class UptimeRepository:
    """Store and retrieve maintenance monitor configs, one document per guild."""

    COLLECTION = "uptime_monitors"
    DOC_ID = "config"

    def _col(self, guild_id: str):
        return mongo_manager.get_guild_collection(guild_id, self.COLLECTION)

    async def load_all(self, guild_ids: list[str]) -> dict[str, dict]:
        """Return ``{guild_id: {sensor_id: monitor_config, ...}, ...}``."""
        result: dict[str, dict] = {}
        for guild_id in guild_ids:
            try:
                doc = await self._col(guild_id).find_one({"_id": self.DOC_ID})
                if doc:
                    result[guild_id] = {k: v for k, v in doc.items() if k != "_id"}
            except Exception as e:
                logger.error("Failed to load uptime monitors for guild %s: %s", guild_id, e)
        return result

    async def save_all(self, monitors: dict[str, dict]) -> None:
        for guild_id, sensors in monitors.items():
            try:
                await self._col(guild_id).update_one(
                    {"_id": self.DOC_ID}, {"$set": sensors}, upsert=True
                )
            except Exception as e:
                logger.error("Failed to save uptime monitors for guild %s: %s", guild_id, e)
