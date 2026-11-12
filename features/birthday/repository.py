"""Birthday repository — all MongoDB I/O for the birthday feature."""

import pymongo

from features.birthday.models import BirthdayEntry
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger, log_failure

logger = init_logger(__name__)


class BirthdayRepository:
    def __init__(self, guild_id) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "birthday")

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index([("user", pymongo.ASCENDING)], unique=True)
        except Exception as e:
            log_failure(
                logger, "Failed to create birthday index for guild %s: %s", self._guild_id, e
            )

    @translates_db_errors
    async def find_one(self, user_id: int) -> BirthdayEntry | None:
        doc = await self._col().find_one({"user": user_id}, {"_id": 0})
        return BirthdayEntry(**doc) if doc else None

    @translates_db_errors
    async def find_all(self) -> list[BirthdayEntry]:
        entries = []
        for doc in await self._col().find({}, {"_id": 0}).to_list(length=None):
            try:
                entries.append(BirthdayEntry(**doc))
            except Exception as e:
                logger.warning("Skipping malformed birthday document: %s", e)
        return entries

    @translates_db_errors
    async def upsert(self, entry: BirthdayEntry) -> None:
        await self._col().update_one(
            {"user": entry.user}, {"$set": entry.model_dump()}, upsert=True
        )

    @translates_db_errors
    async def update_fields(self, user_id: int, fields: dict) -> None:
        await self._col().update_one({"user": user_id}, {"$set": fields})

    @translates_db_errors
    async def delete(self, user_id: int) -> int:
        result = await self._col().delete_one({"user": user_id})
        return result.deleted_count
