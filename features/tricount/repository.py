"""Tricount repository — all MongoDB I/O for the tricount feature."""

from datetime import datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from src.core.db import mongo_manager, translates_db_errors

GROUPS_COLLECTION = "tricount_groups"
EXPENSES_COLLECTION = "tricount_expenses"
RECURRING_COLLECTION = "tricount_recurring"


def _as_object_id(value: str | ObjectId) -> ObjectId | None:
    """Parse a user-supplied id into an ``ObjectId``; ``None`` when malformed."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


class TricountRepository:
    """Per-guild data access for tricount groups, expenses, and recurring templates.

    Methods are thin pass-throughs to MongoDB: query filters, update documents,
    and return shapes mirror the underlying motor calls (raw dicts). Driver
    errors are translated to :class:`~src.core.errors.DatabaseError` so callers
    in ``extensions/`` never import ``pymongo``.

    Methods that take an id a user typed accept it as a plain ``str`` and parse
    it here — ``bson`` stays behind this boundary, and a malformed id is an
    ordinary "not found" rather than an ``InvalidId`` escaping into a command
    handler.
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

    @translates_db_errors
    async def find_group_by_name(self, name: str) -> dict | None:
        """Find a group by name, active or not."""
        return await self._col(GROUPS_COLLECTION).find_one({"name": name})

    @translates_db_errors
    async def find_active_group(self, name: str) -> dict | None:
        """Find an active group by name."""
        return await self._col(GROUPS_COLLECTION).find_one({"name": name, "is_active": True})

    @translates_db_errors
    async def find_active_group_by_id(self, group_id: ObjectId) -> dict | None:
        """Find an active group by its ``_id``."""
        return await self._col(GROUPS_COLLECTION).find_one({"_id": group_id, "is_active": True})

    @translates_db_errors
    async def create_group(self, data: dict) -> Any:
        """Insert a group document and return its inserted ``_id``."""
        result = await self._col(GROUPS_COLLECTION).insert_one(data)
        return result.inserted_id

    @translates_db_errors
    async def add_group_member(self, group_id: ObjectId, user_id: int) -> None:
        """Append a user to a group's member list."""
        await self._col(GROUPS_COLLECTION).update_one(
            {"_id": group_id}, {"$push": {"members": user_id}}
        )

    @translates_db_errors
    async def remove_group_member(self, group_id: ObjectId, user_id: int) -> None:
        """Remove a user from a group's member list."""
        await self._col(GROUPS_COLLECTION).update_one(
            {"_id": group_id}, {"$pull": {"members": user_id}}
        )

    @translates_db_errors
    async def list_member_groups(self, user_id: int) -> list[dict]:
        """All active groups the user is a member of."""
        return (
            await self._col(GROUPS_COLLECTION)
            .find({"is_active": True, "members": user_id})
            .to_list(length=None)
        )

    # --- Expenses -----------------------------------------------------

    @translates_db_errors
    async def add_expense(self, data: dict) -> None:
        """Insert an expense document."""
        await self._col(EXPENSES_COLLECTION).insert_one(data)

    @translates_db_errors
    async def find_expense_in_group(
        self, expense_id: str | ObjectId, group_id: ObjectId
    ) -> dict | None:
        """Find an expense by ``_id``, scoped to a group. ``None`` if the id is malformed."""
        parsed = _as_object_id(expense_id)
        if parsed is None:
            return None
        return await self._col(EXPENSES_COLLECTION).find_one({"_id": parsed, "group_id": group_id})

    @translates_db_errors
    async def update_expense_fields(self, expense_id: str | ObjectId, fields: dict) -> bool:
        """``$set`` the given fields on an expense; ``False`` if it matched nothing."""
        parsed = _as_object_id(expense_id)
        if parsed is None:
            return False
        result = await self._col(EXPENSES_COLLECTION).update_one({"_id": parsed}, {"$set": fields})
        return result.matched_count > 0

    @translates_db_errors
    async def list_group_expenses(self, group_id: ObjectId) -> list[dict]:
        """All expenses of a group."""
        return (
            await self._col(EXPENSES_COLLECTION).find({"group_id": group_id}).to_list(length=None)
        )

    @translates_db_errors
    async def list_recent_expenses(self, group_id: ObjectId, limit: int) -> list[dict]:
        """Latest expenses of a group, newest first."""
        return (
            await self._col(EXPENSES_COLLECTION)
            .find({"group_id": group_id})
            .sort("date", -1)
            .limit(limit)
            .to_list(length=None)
        )

    @translates_db_errors
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

    @translates_db_errors
    async def count_group_expenses(self, group_id: ObjectId) -> int:
        """Number of expenses recorded for a group."""
        return await self._col(EXPENSES_COLLECTION).count_documents({"group_id": group_id})

    @translates_db_errors
    async def distinct_categories(self) -> list:
        """Distinct ``category`` values used by this guild's expenses."""
        return await self._col(EXPENSES_COLLECTION).distinct("category")

    # --- Recurring expenses ---------------------------------------------

    @translates_db_errors
    async def ensure_recurring_indexes(self) -> None:
        """Create the ``next_run`` and ``active`` indexes on the recurring collection."""
        await self._col(RECURRING_COLLECTION).create_index("next_run", name="next_run_idx")
        await self._col(RECURRING_COLLECTION).create_index("active", name="active_idx")

    @translates_db_errors
    async def add_recurring(self, data: dict) -> Any:
        """Insert a recurring-expense template and return its inserted ``_id``."""
        result = await self._col(RECURRING_COLLECTION).insert_one(data)
        return result.inserted_id

    @translates_db_errors
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

    @translates_db_errors
    async def list_due_recurring(self, now: datetime) -> list[dict]:
        """Active recurring templates whose ``next_run`` is due."""
        return (
            await self._col(RECURRING_COLLECTION)
            .find({"active": True, "next_run": {"$lte": now}})
            .to_list(length=None)
        )

    @translates_db_errors
    async def stop_recurring(self, recurring_id: str | ObjectId, user_id: int) -> int:
        """Deactivate the user's active recurrence; returns the modified count.

        A malformed id counts as zero rows modified — the caller's "introuvable"
        path already says the right thing.
        """
        parsed = _as_object_id(recurring_id)
        if parsed is None:
            return 0
        result = await self._col(RECURRING_COLLECTION).update_one(
            {"_id": parsed, "added_by": user_id, "active": True},
            {"$set": {"active": False}},
        )
        return result.modified_count

    @translates_db_errors
    async def deactivate_recurring(self, recurring_id: ObjectId) -> None:
        """Deactivate a recurrence unconditionally (e.g. its group was deleted)."""
        await self._col(RECURRING_COLLECTION).update_one(
            {"_id": recurring_id}, {"$set": {"active": False}}
        )

    @translates_db_errors
    async def reschedule_recurring(self, recurring_id: ObjectId, next_run: datetime) -> None:
        """Set the next occurrence date of a recurrence."""
        await self._col(RECURRING_COLLECTION).update_one(
            {"_id": recurring_id}, {"$set": {"next_run": next_run}}
        )
