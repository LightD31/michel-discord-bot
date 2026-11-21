"""The members who wish a newcomer welcome (A8).

Under a welcome message, "👋 Souhaiter la bienvenue": each click adds the
member to that message's ``welcome_greetings`` document (``_id`` the message
id), and the message says how many did. A document lives 7 days (TTL on
``expires_at``); after that the button answers that it's too late and the
message keeps the count it shows.

What is stored: the newcomer's id and the ids of those who greeted them.
``/mes-donnees supprimer`` deletes a newcomer's document and takes a greeter
out of the others (``features/userdata/repository.py``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

import pymongo
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger, log_failure

logger = init_logger(__name__)

COLLECTION = "welcome_greetings"
KEEP_FOR = timedelta(days=7)
# Names shown on the message; the others are counted.
NAMES_SHOWN = 5
# The line the bot adds under the welcome text, so it can rewrite it.
GREETING_MARK = "-# 👋 "

GreetRefusal = Literal["expired", "self", "already"]


class Greeting(BaseModel):
    message_id: str = Field(alias="_id")
    member_id: str
    greeters: list[str] = Field(default_factory=list)
    expires_at: datetime

    model_config = {"populate_by_name": True}


def greeting_line(names: list[str], total: int) -> str:
    """« 👋 Léa, Hugo et 3 autres te souhaitent la bienvenue », as subtext."""
    if total <= 0:
        return ""
    shown = [n for n in names if n][:NAMES_SHOWN]
    others = total - len(shown)
    if not shown:
        who = f"{total} personne{'s' if total > 1 else ''}"
    elif others > 0:
        who = f"{', '.join(shown)} et {others} autre{'s' if others > 1 else ''}"
    elif len(shown) == 1:
        who = shown[0]
    else:
        who = f"{', '.join(shown[:-1])} et {shown[-1]}"
    verb = "te souhaite" if total == 1 else "te souhaitent"
    return f"{GREETING_MARK}{who} {verb} la bienvenue"


def with_greetings(content: str, line: str) -> str:
    """*content* with its greeting line replaced by *line* (added if it had none)."""
    lines = (content or "").split("\n")
    if lines and lines[-1].startswith(GREETING_MARK):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    base = "\n".join(lines)
    if not line:
        return base
    return f"{base}\n\n{line}" if base else line


class GreetingRepository:
    """Per-guild store of who greeted each newcomer."""

    def __init__(self, guild_id: str | int) -> None:
        self._guild_id = str(guild_id)

    def _col(self):
        return mongo_manager.get_guild_collection(self._guild_id, COLLECTION)

    async def ensure_indexes(self) -> None:
        try:
            await self._col().create_index(
                [("expires_at", pymongo.ASCENDING)], expireAfterSeconds=0, name="expires_ttl"
            )
            await self._col().create_index([("member_id", pymongo.ASCENDING)], name="member_idx")
            await self._col().create_index([("greeters", pymongo.ASCENDING)], name="greeters_idx")
        except Exception as e:
            log_failure(logger, "Failed to create greeting indexes for %s: %s", self._guild_id, e)

    @translates_db_errors
    async def open(self, message_id: str, member_id: str, now: datetime) -> None:
        """Start counting the greetings of the welcome message *message_id*."""
        await self._col().update_one(
            {"_id": str(message_id)},
            {
                "$setOnInsert": {
                    "member_id": str(member_id),
                    "greeters": [],
                    "expires_at": now + KEEP_FOR,
                }
            },
            upsert=True,
        )

    @translates_db_errors
    async def greet(
        self, message_id: str, greeter_id: str, now: datetime
    ) -> Greeting | GreetRefusal:
        """Add *greeter_id*; the document after, or why nothing changed.

        One update, filtered on what it requires: still open, not the newcomer,
        not already among the greeters. Two clicks at once add the member once.
        """
        greeter = str(greeter_id)
        doc = await self._col().find_one_and_update(
            {
                "_id": str(message_id),
                "expires_at": {"$gt": now},
                "member_id": {"$ne": greeter},
                "greeters": {"$ne": greeter},
            },
            {"$push": {"greeters": greeter}},
            return_document=ReturnDocument.AFTER,
        )
        if doc:
            return Greeting.model_validate(doc)
        current = await self._col().find_one({"_id": str(message_id)})
        if not current or current["expires_at"] <= now:
            return "expired"
        return "self" if current.get("member_id") == greeter else "already"


__all__ = [
    "COLLECTION",
    "GREETING_MARK",
    "KEEP_FOR",
    "GreetRefusal",
    "Greeting",
    "GreetingRepository",
    "greeting_line",
    "with_greetings",
]
