"""UserInfo repository — keeps the per-guild `users` collection in sync."""

import os

import pymongo
import pymongo.errors

from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))


class UserInfoRepository:
    def __init__(self, guild_id: str) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "users")

    @translates_db_errors
    async def bulk_upsert(self, members_data: list[dict]) -> None:
        """Upsert a batch of members. Each dict must have id, username, display_name."""
        if not members_data:
            return
        ops = [
            pymongo.UpdateOne(
                {"_id": d["id"]},
                {"$set": {"username": d["username"], "display_name": d["display_name"]}},
                upsert=True,
            )
            for d in members_data
        ]
        try:
            await self._col().bulk_write(ops, ordered=False)
            logger.info("Synced %d users for guild %s", len(ops), self._guild_id)
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to bulk-sync users for guild %s: %s", self._guild_id, e)

    @translates_db_errors
    async def upsert(self, user_id: str, username: str, display_name: str) -> None:
        try:
            await self._col().update_one(
                {"_id": user_id},
                {"$set": {"username": username, "display_name": display_name}},
                upsert=True,
            )
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to upsert user %s in guild %s: %s", user_id, self._guild_id, e)

    @translates_db_errors
    async def get_names(self, user_ids: list[str]) -> dict[str, dict[str, str]]:
        """Map each known id in ``user_ids`` to ``{"username", "display_name"}``."""
        if not user_ids:
            return {}
        cursor = self._col().find(
            {"_id": {"$in": list(user_ids)}}, {"username": 1, "display_name": 1}
        )
        return {
            str(doc["_id"]): {
                "username": doc.get("username") or "",
                "display_name": doc.get("display_name") or "",
            }
            async for doc in cursor
        }

    @translates_db_errors
    async def delete(self, user_id: str) -> None:
        try:
            await self._col().delete_one({"_id": user_id})
        except pymongo.errors.PyMongoError as e:
            logger.warning("Failed to delete user %s in guild %s: %s", user_id, self._guild_id, e)
