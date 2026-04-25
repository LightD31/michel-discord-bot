"""MongoDB I/O for the reaction-roles feature — one document per role menu."""

import os
from typing import Any

import pymongo
from bson import ObjectId

from features.reactionroles.models import RoleMenu
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))

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
            logger.error("Failed to create role_menus indexes for guild %s: %s", self._guild_id, e)

    @staticmethod
    def _doc_to_menu(doc: dict) -> RoleMenu:
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return RoleMenu(**doc)

    async def add(self, menu: RoleMenu) -> str:
        try:
            payload = menu.model_dump(exclude={"id"})
            result = await self._col().insert_one(payload)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("DB insert_one failed: %s", e)
            raise DatabaseError(f"Failed to add role menu: {e}") from e

    async def get(self, menu_id: str) -> RoleMenu | None:
        try:
            doc = await self._col().find_one({"_id": ObjectId(menu_id)})
        except Exception as e:
            logger.error("DB find_one failed: %s", e)
            return None
        return self._doc_to_menu(doc) if doc else None

    async def list(self) -> list[RoleMenu]:
        try:
            cursor = self._col().find().sort("created_at", pymongo.DESCENDING)
            docs = await cursor.to_list(length=None)
            return [self._doc_to_menu(d) for d in docs]
        except Exception as e:
            logger.error("DB find failed: %s", e)
            raise DatabaseError(f"Failed to list role menus: {e}") from e

    async def update(self, menu_id: str, **fields: Any) -> int:
        if not fields:
            return 0
        try:
            result = await self._col().update_one(
                {"_id": ObjectId(menu_id)}, {"$set": fields}
            )
            return result.modified_count
        except Exception as e:
            logger.error("DB update_one failed: %s", e)
            raise DatabaseError(f"Failed to update role menu: {e}") from e

    async def delete(self, menu_id: str) -> int:
        try:
            result = await self._col().delete_one({"_id": ObjectId(menu_id)})
            return result.deleted_count
        except Exception as e:
            logger.error("DB delete_one failed: %s", e)
            raise DatabaseError(f"Failed to delete role menu: {e}") from e
