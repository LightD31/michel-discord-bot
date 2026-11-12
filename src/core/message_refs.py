"""Where each message the bot keeps up to date lives, per guild.

A status, a schedule, a leaderboard: the bot posts each of these once and then
edits it. Which message that is (its channel and id) is runtime state, not
configuration, so it lives in the guild's database (collection
``bot_messages``) rather than in ``config.json``, and goes with the backups.

Each message has a *slot*, a stable name chosen by the code that owns it
(``"satisfactory"``, ``"speedons:live"``, ``"vlrgg:team:<name>"``). The slot is
the document's ``_id``, a value rather than a field path, so any string works.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.core.db import mongo_manager, translates_db_errors

COLLECTION = "bot_messages"


@dataclass(frozen=True)
class MessageRef:
    """One managed message: where it is, and what the dashboard calls it."""

    slot: str
    channel_id: str
    message_id: str
    label: str = ""

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> MessageRef:
        return cls(
            slot=str(doc["_id"]),
            channel_id=str(doc.get("channel_id") or ""),
            message_id=str(doc.get("message_id") or ""),
            label=str(doc.get("label") or ""),
        )


class MessageRefRepository:
    """Per-guild accessor for the ``bot_messages`` collection."""

    def __init__(self, guild_id: str | int) -> None:
        self.guild_id = guild_id

    def _col(self) -> Any:
        return mongo_manager.get_guild_collection(self.guild_id, COLLECTION)

    @translates_db_errors
    async def get(self, slot: str) -> MessageRef | None:
        doc = await self._col().find_one({"_id": slot})
        return MessageRef.from_mongo(doc) if doc else None

    @translates_db_errors
    async def save(self, slot: str, channel_id: str, message_id: str, label: str = "") -> None:
        await self._col().update_one(
            {"_id": slot},
            {
                "$set": {
                    "channel_id": str(channel_id),
                    "message_id": str(message_id),
                    "label": label,
                    "updated_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    @translates_db_errors
    async def delete(self, slot: str) -> MessageRef | None:
        """Forget the slot; returns what it pointed at, ``None`` if it was unknown."""
        doc = await self._col().find_one_and_delete({"_id": slot})
        return MessageRef.from_mongo(doc) if doc else None

    @translates_db_errors
    async def list(self) -> list[MessageRef]:
        docs = await self._col().find({}).sort("_id", 1).to_list()
        return [MessageRef.from_mongo(doc) for doc in docs]


__all__ = ["COLLECTION", "MessageRef", "MessageRefRepository"]
