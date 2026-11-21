"""A module's earlier settings, to see what a save changed and put it back (C5).

The audit log says who saved a module and which fields changed, never the
values. This keeps the values: each save from the dashboard stores the
module's settings as they were just before, in ``guild_<id>.config_history``,
for :data:`RETENTION` (TTL index). Secret fields are left out, so a password
or an API key is never copied; restoring a version keeps the module's current
secrets (``restore_section``, which also refuses an old URL with today's
password). The guild's database holds it, so the 30-day purge after the bot
leaves the guild and the backups cover it like the rest. No user id is
stored: the audit log already says who saved.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import pymongo
from bson import ObjectId
from bson.errors import InvalidId

from src.core import clock
from src.core import logging as logutil
from src.core.db import mongo_manager, translates_db_errors
from src.core.errors import DatabaseError
from src.webui.secrets import secret_field_names

logger = logutil.init_logger("webui.config_history")

COLLECTION = "config_history"
RETENTION = timedelta(days=90)
# Versions listed per module: the latest ones, which is what a restore is for.
LIST_LIMIT = 20
_indexed: set[str] = set()


def without_secrets(settings: Any, schema: dict[str, Any] | None) -> dict[str, Any]:
    """A copy of *settings* without the schema's secret fields."""
    if not isinstance(settings, dict):
        return {}
    secrets = secret_field_names(schema)
    return {k: copy.deepcopy(v) for k, v in settings.items() if k not in secrets}


class ConfigHistoryRepository:
    """One guild's earlier module settings."""

    def __init__(self, guild_id: str) -> None:
        self.guild_id = guild_id

    def _col(self) -> Any:
        return mongo_manager.get_guild_collection(self.guild_id, COLLECTION)

    @translates_db_errors
    async def ensure_indexes(self) -> None:
        col = self._col()
        await col.create_index(
            "at", name="at_ttl", expireAfterSeconds=int(RETENTION.total_seconds())
        )
        await col.create_index([("module", pymongo.ASCENDING), ("_id", pymongo.DESCENDING)])

    @translates_db_errors
    async def add(self, module: str, settings: dict[str, Any]) -> None:
        if self.guild_id not in _indexed:
            await self.ensure_indexes()
            _indexed.add(self.guild_id)
        await self._col().insert_one({"module": module, "at": clock.now(), "settings": settings})

    @translates_db_errors
    async def list(self, module: str, *, limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
        """The module's versions, newest first."""
        cursor = self._col().find({"module": module}).sort("_id", pymongo.DESCENDING)
        return list(await cursor.limit(max(1, min(limit, LIST_LIMIT))).to_list())

    @translates_db_errors
    async def get(self, module: str, version_id: str) -> dict[str, Any] | None:
        """One version of *module*, ``None`` for an id that names none (or isn't one)."""
        try:
            oid = ObjectId(version_id)
        except (InvalidId, TypeError):
            return None
        return await self._col().find_one({"_id": oid, "module": module})


async def remember(guild_id: str, module: str, before: Any, schema: dict[str, Any] | None) -> None:
    """Keep *before*, the settings a save replaced; a failure is logged, never raised."""
    if not isinstance(before, dict) or not before:
        return  # nothing was set: nothing to go back to
    try:
        await ConfigHistoryRepository(guild_id).add(module, without_secrets(before, schema))
    except (DatabaseError, RuntimeError) as e:  # RuntimeError: no MongoDB configured
        logger.warning("Earlier %s settings of %s not kept: %s", module, guild_id, e)


def version_to_json(version: dict[str, Any], newer: Any) -> dict[str, Any]:
    """A version for the SPA, with the fields that differ from the one after it."""
    raw = version.get("settings")
    settings: dict[str, Any] = raw if isinstance(raw, dict) else {}
    after: dict[str, Any] = newer if isinstance(newer, dict) else {}
    changed = sorted(k for k in settings.keys() | after.keys() if settings.get(k) != after.get(k))
    at = version.get("at")
    return {
        "id": str(version.get("_id", "")),
        "at": at.isoformat() if at else None,
        "settings": settings,
        "changed": changed,
    }
