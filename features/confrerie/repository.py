"""Confrérie drafts repository — all MongoDB I/O for pending submissions."""

import os

import pymongo.errors

from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger

from .drafts import STATUS_PENDING, STATUS_PROCESSING, Draft

logger = init_logger(os.path.basename(__file__))


class DraftRepository:
    """Per-guild ``confrerie_drafts`` collection. A draft's id is its dedupe key."""

    def __init__(self, guild_id: str) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, "confrerie_drafts")

    @translates_db_errors
    async def insert_if_new(self, draft: Draft) -> bool:
        """Store ``draft``; False when one with the same id already exists."""
        try:
            await self._col().insert_one(draft.to_doc())
        except pymongo.errors.DuplicateKeyError:
            return False
        return True

    @translates_db_errors
    async def get(self, draft_id: str) -> Draft | None:
        doc = await self._col().find_one({"_id": draft_id})
        return Draft.from_doc(doc) if doc else None

    @translates_db_errors
    async def save(self, draft: Draft) -> None:
        await self._col().replace_one({"_id": draft.id}, draft.to_doc(), upsert=True)

    @translates_db_errors
    async def claim(self, draft_id: str) -> Draft | None:
        """Atomically move a pending draft to *processing* and return it.

        Returns None when the draft is gone or someone else already handled it —
        a double click on "Valider" must not create two Notion pages.
        """
        doc = await self._col().find_one_and_update(
            {"_id": draft_id, "status": STATUS_PENDING},
            {"$set": {"status": STATUS_PROCESSING}},
            return_document=pymongo.ReturnDocument.AFTER,
        )
        return Draft.from_doc(doc) if doc else None

    @translates_db_errors
    async def delete(self, draft_id: str) -> None:
        await self._col().delete_one({"_id": draft_id})

    @translates_db_errors
    async def set_status(self, draft_id: str, status: str, **fields: object) -> None:
        await self._col().update_one({"_id": draft_id}, {"$set": {"status": status, **fields}})
