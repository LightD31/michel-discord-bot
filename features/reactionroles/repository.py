"""MongoDB I/O for the reaction-roles feature — one document per role menu."""

from typing import Any

import pymongo
from bson import ObjectId
from bson.errors import InvalidId

from features.reactionroles.models import RoleMenu
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger, log_failure

logger = init_logger(__name__)

COLLECTION = "role_menus"


class ReactionRolesRepository:
    """Per-guild store for role menus."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index(
                [("message_id", pymongo.ASCENDING)], name="message_id_idx"
            )
        except Exception as e:
            log_failure(
                logger, "Failed to create role_menus indexes for guild %s: %s", self._guild_id, e
            )

    @staticmethod
    def _doc_to_menu(doc: dict) -> RoleMenu:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return RoleMenu(**doc)

    @staticmethod
    def _oid(menu_id: str) -> ObjectId | None:
        """The menu's ``ObjectId``, ``None`` for an id that can't be one (a typo)."""
        try:
            return ObjectId(menu_id)
        except (InvalidId, TypeError):
            return None

    @translates_db_errors
    async def add(self, menu: RoleMenu) -> str:
        result = await self._col().insert_one(menu.model_dump(exclude={"id"}))
        return str(result.inserted_id)

    @translates_db_errors
    async def get(self, menu_id: str) -> RoleMenu | None:
        """The menu, ``None`` when there is none with that id.

        A MongoDB outage raises ``DatabaseError``: it used to be swallowed into
        ``None`` too, and the member was told the menu no longer existed.
        """
        oid = self._oid(menu_id)
        if oid is None:
            return None
        doc = await self._col().find_one({"_id": oid})
        return self._doc_to_menu(doc) if doc else None

    @translates_db_errors
    async def list(self) -> list[RoleMenu]:
        cursor = self._col().find().sort("created_at", pymongo.DESCENDING)
        return [self._doc_to_menu(d) for d in await cursor.to_list()]

    @translates_db_errors
    async def update(self, menu_id: str, **fields: Any) -> int:
        oid = self._oid(menu_id)
        if not fields or oid is None:
            return 0
        result = await self._col().update_one({"_id": oid}, {"$set": fields})
        return result.modified_count

    @translates_db_errors
    async def delete(self, menu_id: str) -> int:
        oid = self._oid(menu_id)
        if oid is None:
            return 0
        result = await self._col().delete_one({"_id": oid})
        return result.deleted_count
