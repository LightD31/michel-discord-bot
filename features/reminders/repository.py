"""MongoDB I/O for the reminder feature — one document per reminder, TTL-expired."""

import os
from datetime import datetime

import pymongo
from bson import ObjectId

from features.reminders.models import Reminder
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

COLLECTION = "task_reminders"
# Grace period after a reminder fires before Mongo's TTL monitor deletes it.
# The scheduled check task normally deletes fired reminders within a minute, so
# this is a safety net for docs the task never processed (e.g. bot was offline).
TTL_GRACE_SECONDS = 86_400


class ReminderRepository:
    """Per-guild store for scheduled reminders."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index(
                [("remind_time", pymongo.ASCENDING)],
                expireAfterSeconds=TTL_GRACE_SECONDS,
                name="remind_time_ttl",
            )
            await self._col().create_index([("user_id", pymongo.ASCENDING)], name="user_id_idx")
        except Exception as e:
            logger.error("Failed to create reminder indexes for guild %s: %s", self._guild_id, e)

    @staticmethod
    def _doc_to_reminder(doc: dict) -> Reminder:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Reminder(**doc)

    async def add(self, reminder: Reminder) -> str:
        try:
            payload = reminder.model_dump(exclude={"id"})
            result = await self._col().insert_one(payload)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to add reminder: {e}") from e

    async def delete(self, reminder_id: str) -> int:
        try:
            result = await self._col().delete_one({"_id": ObjectId(reminder_id)})
            return result.deleted_count
        except Exception as e:
            logger.error("DB delete_one failed: %s", e)
            raise DatabaseError(f"Failed to delete reminder: {e}") from e

    async def reschedule(self, reminder_id: str, new_time: datetime) -> None:
        try:
            await self._col().update_one(
                {"_id": ObjectId(reminder_id)},
                {"$set": {"remind_time": new_time}},
            )
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to reschedule reminder: {e}") from e

    async def list_for_user(self, user_id: str) -> list[Reminder]:
        try:
            cursor = self._col().find({"user_id": user_id}).sort("remind_time", pymongo.ASCENDING)
            docs = await cursor.to_list(length=None)
            return [self._doc_to_reminder(d) for d in docs]
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to list reminders: {e}") from e

    async def list_due(self, now: datetime) -> list[Reminder]:
        try:
            cursor = self._col().find({"remind_time": {"$lte": now}})
            docs = await cursor.to_list(length=None)
            return [self._doc_to_reminder(d) for d in docs]
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to list due reminders: {e}") from e
