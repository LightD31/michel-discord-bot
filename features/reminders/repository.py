"""MongoDB I/O for the reminder feature — one document per reminder."""

from datetime import datetime

import pymongo
from bson import ObjectId

from features.reminders.models import Reminder
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger, log_failure

logger = init_logger(__name__)

COLLECTION = "task_reminders"
# No TTL on this collection: a reminder leaves it when it is claimed (a one-off
# one is deleted, a recurring one moves to its next time, see ``claim_due``).
# A TTL on ``remind_time`` deleted the very reminders it was meant to keep:
# every one still due after a day with the bot down (or the module off for the
# guild), recurring ones included, instead of sending them late and moving on.


class ReminderRepository:
    """Per-guild store for scheduled reminders."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index(
                [("remind_time", pymongo.ASCENDING)], name="remind_time_idx"
            )
            await self._col().create_index([("user_id", pymongo.ASCENDING)], name="user_id_idx")
        except Exception as e:
            log_failure(
                logger, "Failed to create reminder indexes for guild %s: %s", self._guild_id, e
            )

    @staticmethod
    def _doc_to_reminder(doc: dict) -> Reminder:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return Reminder(**doc)

    @translates_db_errors
    async def add(self, reminder: Reminder) -> str:
        result = await self._col().insert_one(reminder.model_dump(exclude={"id"}))
        return str(result.inserted_id)

    @translates_db_errors
    async def delete(self, reminder_id: str) -> int:
        result = await self._col().delete_one({"_id": ObjectId(reminder_id)})
        return result.deleted_count

    @translates_db_errors
    async def reschedule(self, reminder_id: str, new_time: datetime) -> None:
        await self._col().update_one(
            {"_id": ObjectId(reminder_id)}, {"$set": {"remind_time": new_time}}
        )

    @translates_db_errors
    async def claim_due(self, reminder: Reminder, next_time: datetime | None) -> bool:
        """Move a due reminder to *next_time* (or delete it), if no one did meanwhile.

        Called before the reminder is sent: the update only matches while the
        reminder still holds the due time it was listed with, so it is sent
        once even if two checks race, and a failed write sends nothing rather
        than the same DM every minute.
        """
        if reminder.id is None:
            return False
        claim = {"_id": ObjectId(reminder.id), "remind_time": reminder.remind_time}
        if next_time is None:
            deleted = await self._col().delete_one(claim)
            return bool(deleted.deleted_count)
        updated = await self._col().update_one(claim, {"$set": {"remind_time": next_time}})
        return bool(updated.modified_count)

    @translates_db_errors
    async def count_for_user(self, user_id: str) -> int:
        return int(await self._col().count_documents({"user_id": user_id}))

    @translates_db_errors
    async def list_for_user(self, user_id: str) -> list[Reminder]:
        cursor = self._col().find({"user_id": user_id}).sort("remind_time", pymongo.ASCENDING)
        return [self._doc_to_reminder(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def list_due(self, now: datetime) -> list[Reminder]:
        cursor = self._col().find({"remind_time": {"$lte": now}})
        return [self._doc_to_reminder(d) for d in await cursor.to_list()]
