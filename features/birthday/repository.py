"""Birthday repository — all MongoDB I/O for the birthday feature."""

import os
from typing import Optional

import pymongo

from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger
from features.birthday.models import BirthdayEntry

logger = init_logger(os.path.basename(__file__))


class BirthdayRepository:
    def __init__(self, guild_id) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "birthday")

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index([("user", pymongo.ASCENDING)], unique=True)
        except Exception as e:
            logger.error("Failed to create birthday index for guild %s: %s", self._guild_id, e)

    async def find_one(self, user_id: int) -> Optional[BirthdayEntry]:
        try:
            doc = await self._col().find_one({"user": user_id})
            if doc is None:
                return None
            doc.pop("_id", None)
            return BirthdayEntry(**doc)
        except Exception as e:
            logger.error("DB find_one failed: %s", e)
            raise DatabaseError(f"Failed to query database: {e}")

    async def find_all(self) -> list[BirthdayEntry]:
        try:
            docs = await self._col().find({}).to_list(length=None)
            entries = []
            for doc in docs:
                doc.pop("_id", None)
                try:
                    entries.append(BirthdayEntry(**doc))
                except Exception as e:
                    logger.warning("Skipping malformed birthday document: %s", e)
            return entries
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to query database: {e}")

    async def upsert(self, entry: BirthdayEntry) -> None:
        try:
            data = entry.model_dump()
            await self._col().update_one(
                {"user": entry.user},
                {"$set": data},
                upsert=True,
            )
        except Exception as e:
            logger.error("DB upsert failed: %s", e)
            raise DatabaseError(f"Failed to upsert birthday: {e}")

    async def update_fields(self, user_id: int, fields: dict) -> None:
        try:
            await self._col().update_one({"user": user_id}, {"$set": fields})
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to update birthday: {e}")

    async def delete(self, user_id: int) -> int:
        try:
            result = await self._col().delete_one({"user": user_id})
            return result.deleted_count
        except Exception as e:
            logger.error("DB delete_one failed: %s", e)
            raise DatabaseError(f"Failed to delete birthday: {e}")
