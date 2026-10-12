"""MongoDB persistence for the !request reminder subscriptions (one document per member)."""

from __future__ import annotations

from typing import Any

from src.core.db import mongo_manager, translates_db_errors

from .models import RequestSubscription


def _hour(value: Any) -> int | None:
    return value if isinstance(value, int) else None


class RequestReminderRepository:
    """Per-guild accessor for the ``zerator247_reminders`` collection."""

    def __init__(self, guild_id: str):
        self.guild_id = guild_id

    def _col(self):
        return mongo_manager.get_guild_collection(self.guild_id, "zerator247_reminders")

    @translates_db_errors
    async def load(self) -> dict[str, RequestSubscription]:
        subscriptions: dict[str, RequestSubscription] = {}
        async for doc in self._col().find({}):
            user_id = str(doc["_id"])
            subscriptions[user_id] = RequestSubscription(
                user_id=user_id,
                start_hour=_hour(doc.get("start_hour")),
                end_hour=_hour(doc.get("end_hour")),
            )
        return subscriptions

    @translates_db_errors
    async def save(self, subscription: RequestSubscription) -> None:
        await self._col().update_one(
            {"_id": subscription.user_id},
            {"$set": {"start_hour": subscription.start_hour, "end_hour": subscription.end_hour}},
            upsert=True,
        )

    @translates_db_errors
    async def delete(self, user_id: str) -> bool:
        result = await self._col().delete_one({"_id": user_id})
        return bool(result.deleted_count)
