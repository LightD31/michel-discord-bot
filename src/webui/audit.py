"""Who changed what from the dashboard, and when (F09).

Every action a dashboard user takes is recorded: a module saved or toggled, a
sanction revoked, a role menu built, embeds published… Guild actions go to
``guild_<id>.dashboard_audit``, which every admin of that guild can read on its
server page, so co-admins see each other's changes; actions on the whole bot
(global sections, extension reloads, short links) go to
``global.dashboard_audit``, read by developers only.

An entry names the actor, the action, its target and the *names* of the fields
a save changed: never a value, so neither a secret nor whatever personal data a
field holds (first names, e-mail addresses) is copied into the log. Entries
expire after :data:`RETENTION` (TTL index). Recording never fails the request
it describes: an outage costs an entry and a warning line, not the change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pymongo
from bson import ObjectId

from src.core import clock
from src.core import logging as logutil
from src.core.db import mongo_manager, translates_db_errors
from src.core.errors import DatabaseError

logger = logutil.init_logger("webui.audit")

COLLECTION = "dashboard_audit"
RETENTION = timedelta(days=365)
PAGE_SIZE = 100
# Details are short labels (an extension name, a case number), never free text.
MAX_DETAIL_LENGTH = 200
# Logs whose TTL index this process already created (one round trip each, not per entry).
_indexed: set[str | None] = set()


def changed_fields(before: Any, after: Any) -> list[str]:
    """The top-level keys whose value differs between two configs, sorted."""
    old = before if isinstance(before, dict) else {}
    new = after if isinstance(after, dict) else {}
    return sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))


def _clean_details(details: dict[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (details or {}).items():
        if isinstance(value, bool | int | float) or value is None:
            clean[key] = value
        else:
            clean[key] = str(value)[:MAX_DETAIL_LENGTH]
    return clean


class AuditRepository:
    """One guild's audit log, or the bot's (``guild_id=None``)."""

    def __init__(self, guild_id: str | None) -> None:
        self.guild_id = guild_id

    def _col(self) -> Any:
        if self.guild_id is None:
            return mongo_manager.get_global_collection(COLLECTION)
        return mongo_manager.get_guild_collection(self.guild_id, COLLECTION)

    @translates_db_errors
    async def ensure_indexes(self) -> None:
        await self._col().create_index(
            "at", name="at_ttl", expireAfterSeconds=int(RETENTION.total_seconds())
        )

    @translates_db_errors
    async def add(self, entry: dict[str, Any]) -> None:
        if self.guild_id not in _indexed:
            await self.ensure_indexes()
            _indexed.add(self.guild_id)
        await self._col().insert_one(entry)

    @translates_db_errors
    async def list(self, *, limit: int = PAGE_SIZE, before: ObjectId | None = None) -> list[dict]:
        """The newest entries first; *before* pages back from an entry's id.

        By id rather than ``at``: entries written in the same millisecond share
        their ``at``, and a page boundary between them would skip some.
        """
        query: dict[str, Any] = {} if before is None else {"_id": {"$lt": before}}
        cursor = self._col().find(query).sort("_id", pymongo.DESCENDING)
        return list(await cursor.limit(max(1, min(limit, PAGE_SIZE))).to_list())

    @translates_db_errors
    async def last_changes(self, actions: tuple[str, ...]) -> dict[str, datetime]:
        """Each target's newest entry among *actions*: when a module was last changed (C2)."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"action": {"$in": list(actions)}, "target": {"$type": "string"}}},
            {"$group": {"_id": "$target", "at": {"$max": "$at"}}},
        ]
        rows = await (await self._col().aggregate(pipeline)).to_list()
        return {row["_id"]: row["at"] for row in rows if isinstance(row.get("at"), datetime)}

    @translates_db_errors
    async def has(self, action: str, target: str | None = None) -> bool:
        """Whether *action* (on *target*, when given) was ever recorded, within the retention."""
        query: dict[str, Any] = {"action": action}
        if target is not None:
            query["target"] = target
        return await self._col().find_one(query, projection={"_id": 1}) is not None


async def record(
    guild_id: str | None,
    session: Any,
    action: str,
    *,
    target: str | None = None,
    changes: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add one entry for *session*'s *action*; a failure is logged, never raised."""
    entry = {
        "at": clock.now(),
        "user_id": str(getattr(session, "user_id", "") or ""),
        "username": str(getattr(session, "username", "") or ""),
        "action": action,
        "target": target,
        "changes": list(changes or []),
        "details": _clean_details(details),
    }
    try:
        await AuditRepository(guild_id).add(entry)
    except (DatabaseError, RuntimeError) as e:  # RuntimeError: no MongoDB configured
        logger.warning("Audit entry %s (guild %s) not recorded: %s", action, guild_id, e)


def entry_to_json(entry: dict[str, Any]) -> dict[str, Any]:
    at = entry.get("at")
    return {
        "id": str(entry.get("_id", "")),
        "at": at.isoformat() if isinstance(at, datetime) else None,
        "user_id": entry.get("user_id"),
        "username": entry.get("username"),
        "action": entry.get("action"),
        "target": entry.get("target"),
        "changes": list(entry.get("changes") or []),
        "details": dict(entry.get("details") or {}),
    }


__all__ = [
    "RETENTION",
    "AuditRepository",
    "changed_fields",
    "entry_to_json",
    "record",
]
