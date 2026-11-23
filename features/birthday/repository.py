"""Birthday repository — all MongoDB I/O for the birthday feature."""

import pymongo

from features.birthday.models import BirthdayEntry
from src.core.db import GuildRepository, IndexSpec, translates_db_errors
from src.core.logging import init_logger

logger = init_logger(__name__)


class BirthdayRepository(GuildRepository):
    COLLECTION = "birthday"

    def indexes(self) -> list[IndexSpec]:
        return [
            ("", [("user", pymongo.ASCENDING)], {"unique": True}),
        ]

    @translates_db_errors
    async def find_one(self, user_id: int) -> BirthdayEntry | None:
        doc = await self._col().find_one({"user": user_id}, {"_id": 0})
        return BirthdayEntry(**doc) if doc else None

    @translates_db_errors
    async def find_all(self) -> list[BirthdayEntry]:
        entries = []
        for doc in await self._col().find({}, {"_id": 0}).to_list():
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
    async def set_own(self, entry: BirthdayEntry) -> None:
        """Save a member's birthday from the member space, in one update.

        Unlike :meth:`upsert`, today's ``isBirthday`` mark is left as it is, so
        changing one's time zone on the day doesn't wish it a second time.
        """
        await self._col().update_one(
            {"user": entry.user},
            {
                "$set": {
                    "date": entry.date,
                    "timezone": entry.timezone,
                    "hideyear": entry.hideyear,
                },
                "$setOnInsert": {"isBirthday": False},
            },
            upsert=True,
        )

    @translates_db_errors
    async def update_fields(self, user_id: int, fields: dict) -> None:
        await self._col().update_one({"user": user_id}, {"$set": fields})

    @translates_db_errors
    async def mark_celebrated(self, user_id: int) -> bool:
        """Mark today's birthday celebrated; ``False`` when it already was."""
        result = await self._col().update_one(
            {"user": user_id, "isBirthday": {"$ne": True}}, {"$set": {"isBirthday": True}}
        )
        return bool(result.modified_count)

    @translates_db_errors
    async def delete(self, user_id: int) -> int:
        result = await self._col().delete_one({"user": user_id})
        return result.deleted_count
