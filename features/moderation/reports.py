"""Messages members report to the moderators (P4, A7).

A member right-clicks a message, "🚩 Signaler", and writes why. Every report
of the same message lands on one card in the mod-log channel: one
``reports`` document per reported message (``_id`` is the message id), with
the members who reported it and their reasons. A moderator handles it from
the card (message deleted, member warned or timed out, or dismissed) and the
document records who did and with which case.

What is stored is a moderation record: the message's author and channel, who
reported it, why, and how it was handled. Not the message's text: the card
quotes it in Discord, and the bot keeps no copy. A report is deleted a year
after the message was first reported (TTL on ``expires_at``). On
``/mes-donnees supprimer`` a reporter's id becomes ``NOBODY`` (their reason
stays, the report still counts); the reported author's side is kept, like an
infraction (``features/userdata/repository.py``).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Literal

import pymongo
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from src.core.db import GuildRepository, IndexSpec, translates_db_errors
from src.core.logging import init_logger

logger = init_logger(__name__)

COLLECTION = "reports"
KEEP_FOR = timedelta(days=365)
# Discord's cap on a text input, and plenty for "why".
MAX_REASON_LENGTH = 500
# A member reports at most this many messages per window and server.
REPORTS_PER_WINDOW = 5
REPORT_WINDOW = timedelta(minutes=10)

ReportStatus = Literal["open", "deleted", "warned", "timed_out", "dismissed"]


class Reporter(BaseModel):
    user_id: str
    reason: str
    at: datetime


class MessageReport(BaseModel):
    """Every report of one message, and how the moderators handled it."""

    message_id: str = Field(alias="_id")
    channel_id: str
    author_id: str
    reporters: list[Reporter] = Field(default_factory=list)
    status: ReportStatus = "open"
    handled_by: str | None = None
    handled_at: datetime | None = None
    case_id: int | None = None
    card_channel_id: str | None = None
    card_message_id: str | None = None
    created_at: datetime
    expires_at: datetime

    model_config = {"populate_by_name": True}

    @property
    def is_open(self) -> bool:
        return self.status == "open"


class ReportLimiter:
    """At most :data:`REPORTS_PER_WINDOW` reports per member and server per window.

    In memory: a restart forgets it, which only lets a member report a few
    more messages once.
    """

    def __init__(self, limit: int = REPORTS_PER_WINDOW, window: timedelta = REPORT_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._seen: dict[tuple[str, str], deque[datetime]] = {}

    def _recent(self, guild_id: str, user_id: str, now: datetime) -> deque[datetime]:
        times = self._seen.setdefault((str(guild_id), str(user_id)), deque())
        while times and now - times[0] >= self.window:
            times.popleft()
        return times

    def is_limited(self, guild_id: str, user_id: str, now: datetime) -> bool:
        """Whether the member has used up their reports for now."""
        return len(self._recent(guild_id, user_id, now)) >= self.limit

    def record(self, guild_id: str, user_id: str, now: datetime) -> None:
        """Count a report the member sent (the form opening alone doesn't count)."""
        self._recent(guild_id, user_id, now).append(now)


class ReportRepository(GuildRepository):
    """Per-guild store of reported messages."""

    COLLECTION = COLLECTION

    def indexes(self) -> list[IndexSpec]:
        return [
            (
                "",
                [("expires_at", pymongo.ASCENDING)],
                {"expireAfterSeconds": 0, "name": "expires_ttl"},
            ),
            ("", [("author_id", pymongo.ASCENDING)], {"name": "author_idx"}),
            ("", [("reporters.user_id", pymongo.ASCENDING)], {"name": "reporter_idx"}),
        ]

    @staticmethod
    def _model(doc: dict | None) -> MessageReport | None:
        return MessageReport.model_validate(doc) if doc else None

    @translates_db_errors
    async def add(
        self,
        *,
        message_id: str,
        channel_id: str,
        author_id: str,
        reporter_id: str,
        reason: str,
        now: datetime,
    ) -> MessageReport | None:
        """Add one member's report of a message; ``None`` if they already reported it.

        One update: the first report creates the document, the next ones are
        pushed onto it, and a member already in its reporters matches nothing,
        so the upsert collides with the existing id instead of adding them twice.
        Two first reports at once both try the insert: the one that loses it
        collides too, and tries again once, now that the document exists, so
        only a member already among the reporters gets ``None``.
        """
        for attempt in range(2):
            try:
                return await self._push_report(
                    message_id, channel_id, author_id, reporter_id, reason, now
                )
            except DuplicateKeyError:
                if attempt:
                    return None
        return None

    async def _push_report(
        self,
        message_id: str,
        channel_id: str,
        author_id: str,
        reporter_id: str,
        reason: str,
        now: datetime,
    ) -> MessageReport | None:
        doc = await self._col().find_one_and_update(
            {"_id": str(message_id), "reporters.user_id": {"$ne": str(reporter_id)}},
            {
                "$setOnInsert": {
                    "channel_id": str(channel_id),
                    "author_id": str(author_id),
                    "status": "open",
                    "created_at": now,
                    "expires_at": now + KEEP_FOR,
                },
                "$push": {
                    "reporters": {
                        "user_id": str(reporter_id),
                        "reason": reason[:MAX_REASON_LENGTH],
                        "at": now,
                    }
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return self._model(doc)

    @translates_db_errors
    async def get(self, message_id: str) -> MessageReport | None:
        return self._model(await self._col().find_one({"_id": str(message_id)}))

    @translates_db_errors
    async def set_card(self, message_id: str, channel_id: str, card_id: str) -> None:
        await self._col().update_one(
            {"_id": str(message_id)},
            {"$set": {"card_channel_id": str(channel_id), "card_message_id": str(card_id)}},
        )

    @translates_db_errors
    async def claim(
        self, message_id: str, status: ReportStatus, handler_id: str, now: datetime
    ) -> MessageReport | None:
        """Mark an open report handled; ``None`` when someone else got there first."""
        doc = await self._col().find_one_and_update(
            {"_id": str(message_id), "status": "open"},
            {"$set": {"status": status, "handled_by": str(handler_id), "handled_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return self._model(doc)

    @translates_db_errors
    async def reopen(self, message_id: str, status: ReportStatus) -> None:
        """Undo :meth:`claim` when the action it was claimed for failed."""
        await self._col().update_one(
            {"_id": str(message_id), "status": status},
            {"$set": {"status": "open"}, "$unset": {"handled_by": "", "handled_at": ""}},
        )

    @translates_db_errors
    async def set_case(self, message_id: str, case_id: int) -> None:
        await self._col().update_one({"_id": str(message_id)}, {"$set": {"case_id": case_id}})

    @translates_db_errors
    async def list(self, *, open_only: bool = False, limit: int = 100) -> list[MessageReport]:
        """The latest reported messages (the dashboard's list, P15), newest first."""
        query: dict = {"status": "open"} if open_only else {}
        docs = (
            await self._col()
            .find(query)
            .sort("created_at", pymongo.DESCENDING)
            .limit(limit)
            .to_list()
        )
        return [MessageReport.model_validate(d) for d in docs]

    @translates_db_errors
    async def count_against(self, author_id: str) -> int:
        """How many of the member's messages were reported."""
        return int(await self._col().count_documents({"author_id": str(author_id)}))


__all__ = [
    "KEEP_FOR",
    "MAX_REASON_LENGTH",
    "MessageReport",
    "ReportLimiter",
    "ReportRepository",
    "ReportStatus",
    "Reporter",
]
