"""MongoDB persistence for Web UI sessions.

Sessions previously lived only in :attr:`DiscordOAuth.sessions`, so every bot
restart logged all admins out. This repository mirrors that dict in the
``global`` database (collection ``webui_sessions``): logins/logouts write
through, and the whole collection is loaded back into memory when the
dashboard starts. A TTL index on ``expires_dt`` lets MongoDB drop expired
documents on its own even if the periodic purge never runs.

Documents are raw dicts (``_id`` = session token) — (de)serialization to the
:class:`~src.webui.auth.Session` dataclass lives in ``src.webui.auth`` to keep
this module free of webui imports.
"""

from datetime import UTC, datetime
from typing import Any

from src.core.db import mongo_manager

COLLECTION = "webui_sessions"


class SessionRepository:
    """Global-database store for dashboard sessions."""

    def _col(self):
        return mongo_manager.get_global_collection(COLLECTION)

    async def ensure_indexes(self) -> None:
        await self._col().create_index("expires_dt", expireAfterSeconds=0, name="expires_ttl")

    async def upsert_doc(self, doc: dict[str, Any]) -> None:
        await self._col().replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def delete(self, session_token: str) -> None:
        await self._col().delete_one({"_id": session_token})

    async def delete_expired(self, now_ts: float) -> int:
        result = await self._col().delete_many({"expires_at": {"$lte": now_ts}})
        return int(result.deleted_count)

    async def load_all_docs(self) -> list[dict[str, Any]]:
        return [doc async for doc in self._col().find()]


def expires_dt_from_ts(expires_at: float) -> datetime:
    """Timestamp → aware datetime for the TTL index field."""
    return datetime.fromtimestamp(expires_at, tz=UTC)
