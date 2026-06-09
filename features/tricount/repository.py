"""Tricount repository — all MongoDB I/O for the tricount feature."""

from datetime import datetime
from typing import Any

from bson import ObjectId

from src.core.db import mongo_manager

GROUPS_COLLECTION = "tricount_groups"
EXPENSES_COLLECTION = "tricount_expenses"
RECURRING_COLLECTION = "tricount_recurring"


class TricountRepository:
    """Per-guild data access for tricount groups, expenses, and recurring templates.

    Methods are thin pass-throughs to MongoDB: query filters, update documents,
    and return shapes mirror the underlying motor calls (raw dicts), and errors
    propagate to callers unchanged.
    """

    def __init__(self, guild_id) -> None:
        self._guild_id = str(guild_id)

    def _col(self, name: str):
        return mongo_manager.get_guild_collection(self._guild_id, name)

    # --- Groups -------------------------------------------------------

    @staticmethod
    def groups_collection(guild_id):
        """Raw groups collection accessor.

        Exists solely for ``src.discord_ext.autocomplete.guild_group_autocomplete``,
        which expects a ``guild_id -> collection`` callable. Everything else
        should go through the repository methods below.
        """
        return mongo_manager.get_guild_collection(str(guild_id), GROUPS_COLLECTION)

    async def find_group_by_name(self, name: str) -> dict | None:
        """Find a group by name, active or not."""
        return await self._col(GROUPS_COLLECTION).find_one({"name": name})

    async def find_active_group(self, name: str) -> dict | None:
        """Find an active group by name."""
        return await self._col(GROUPS_COLLECTION).find_one({"name": name, "is_active": True})

    async def find_active_group_by_id(self, group_id: ObjectId) -> dict | None:
        """Find an active group by its ``_id``."""
        return await self._col(GROUPS_COLLECTION).find_one({"_id": group_id, "is_active": True})

    async def create_group(self, data: dict) -> Any:
        """Insert a group document and return its inserted ``_id``."""
        result = await self._col(GROUPS_COLLECTION).insert_one(data)
        return result.inserted_id

    async def add_group_member(self, group_id: ObjectId, user_id: int) -> None:
        """Append a user to a group's member list."""
        await self._col(GROUPS_COLLECTION).update_one(
            {"_id": group_id}, {"$push": {"members": user_id}}
        )

    async def remove_group_member(self, group_id: ObjectId, user_id: int) -> None:
        """Remove a user from a group's member list."""
        await self._col(GROUPS_COLLECTION).update_one(
            {"_id": group_id}, {"$pull": {"members": user_id}}
        )

    async def list_member_groups(self, user_id: int) -> list[dict]:
        """All active groups the user is a member of."""
        return (
            await self._col(GROUPS_COLLECTION)
            .find({"is_active": True, "members": user_id})
            .to_list(length=None)
        )

    # --- Expenses -----------------------------------------------------

    async def add_expense(self, data: dict) -> None:
        """Insert an expense document."""
        await self._col(EXPENSES_COLLECTION).insert_one(data)

    async def find_expense_in_group(self, expense_id: ObjectId, group_id: ObjectId) -> dict | None:
        """Find an expense by ``_id``, scoped to a group."""
        return await self._col(EXPENSES_COLLECTION).find_one(
            {"_id": expense_id, "group_id": group_id}
        )

    async def update_expense_fields(self, expense_id: ObjectId, fields: dict) -> None:
        """``$set`` the given fields on an expense."""
        await self._col(EXPENSES_COLLECTION).update_one({"_id": expense_id}, {"$set": fields})

    async def list_group_expenses(self, group_id: ObjectId) -> list[dict]:
        """All expenses of a group."""
        return (
            await self._col(EXPENSES_COLLECTION).find({"group_id": group_id}).to_list(length=None)
        )

    async def list_recent_expenses(self, group_id: ObjectId, limit: int) -> list[dict]:
        """Latest expenses of a group, newest first."""
        return (
            await self._col(EXPENSES_COLLECTION)
            .find({"group_id": group_id})
            .sort("date", -1)
            .limit(limit)
            .to_list(length=None)
        )

    async def list_user_expenses(
        self, group_ids: list[ObjectId], user_id: int, limit: int = 25
    ) -> list[dict]:
        """Latest expenses across groups that the user added or paid, newest first."""
        return (
            await self._col(EXPENSES_COLLECTION)
            .find(
                {
                    "group_id": {"$in": group_ids},
                    "$or": [{"added_by": user_id}, {"payer": user_id}],
                }
            )
            .sort("date", -1)
            .limit(limit)
            .to_list(length=None)
        )

    async def count_group_expenses(self, group_id: ObjectId) -> int:
        """Number of expenses recorded for a group."""
        return await self._col(EXPENSES_COLLECTION).count_documents({"group_id": group_id})

    async def distinct_categories(self) -> list:
        """Distinct ``category`` values used by this guild's expenses."""
        return await self._col(EXPENSES_COLLECTION).distinct("category")

    # --- Recurring expenses ---------------------------------------------

    async def ensure_recurring_indexes(self) -> None:
        """Create the ``next_run`` and ``active`` indexes on the recurring collection."""
        await self._col(RECURRING_COLLECTION).create_index("next_run", name="next_run_idx")
        await self._col(RECURRING_COLLECTION).create_index("active", name="active_idx")

    async def add_recurring(self, data: dict) -> Any:
        """Insert a recurring-expense template and return its inserted ``_id``."""
        result = await self._col(RECURRING_COLLECTION).insert_one(data)
        return result.inserted_id

    async def list_active_recurring(
        self, user_id: int, group_name: str | None = None
    ) -> list[dict]:
        """The user's active recurring templates, soonest ``next_run`` first."""
        query: dict = {"active": True, "added_by": user_id}
        if group_name:
            query["group_name"] = group_name
        return (
            await self._col(RECURRING_COLLECTION)
            .find(query)
            .sort("next_run", 1)
            .to_list(length=None)
        )

    async def list_due_recurring(self, now: datetime) -> list[dict]:
        """Active recurring templates whose ``next_run`` is due."""
        return (
            await self._col(RECURRING_COLLECTION)
            .find({"active": True, "next_run": {"$lte": now}})
            .to_list(length=None)
        )

    async def stop_recurring(self, recurring_id: ObjectId, user_id: int) -> int:
        """Deactivate the user's active recurrence; returns the modified count."""
        result = await self._col(RECURRING_COLLECTION).update_one(
            {"_id": recurring_id, "added_by": user_id, "active": True},
            {"$set": {"active": False}},
        )
        return result.modified_count

    async def deactivate_recurring(self, recurring_id: ObjectId) -> None:
        """Deactivate a recurrence unconditionally (e.g. its group was deleted)."""
        await self._col(RECURRING_COLLECTION).update_one(
            {"_id": recurring_id}, {"$set": {"active": False}}
        )

    async def reschedule_recurring(self, recurring_id: ObjectId, next_run: datetime) -> None:
        """Set the next occurrence date of a recurrence."""
        await self._col(RECURRING_COLLECTION).update_one(
            {"_id": recurring_id}, {"$set": {"next_run": next_run}}
        )
