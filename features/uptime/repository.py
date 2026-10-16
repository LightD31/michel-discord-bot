"""MongoDB I/O for the Uptime feature — one guild's monitor alerts.

All of a guild's alerts share one document, keyed by monitor id::

    {"_id": "config", "12": {"channel_id": …, "mode": "detailed", "last_status": 1}}

Each write touches only its own monitor's key, so removing an alert really
removes it and a status update never resurrects a removed one.
"""

from __future__ import annotations

from typing import Any

from features.uptime.models import MonitorAlert
from src.core.db import mongo_manager, translates_db_errors
from src.core.logging import init_logger
from src.integrations.uptime_kuma import MonitorStatus

logger = init_logger(__name__)

COLLECTION = "uptime_monitors"
DOC_ID = "config"


class UptimeRepository:
    """Accessor for one guild's alerts."""

    def __init__(self, guild_id: str | int) -> None:
        self.guild_id = str(guild_id)

    def _col(self) -> Any:
        return mongo_manager.get_guild_collection(self.guild_id, COLLECTION)

    @translates_db_errors
    async def load(self) -> dict[int, MonitorAlert]:
        doc = await self._col().find_one({"_id": DOC_ID})
        alerts: dict[int, MonitorAlert] = {}
        for key, value in (doc or {}).items():
            if key == "_id":
                continue
            alert = MonitorAlert.from_mongo(key, value)
            if alert is None:
                logger.warning("Guild %s: ignoring malformed uptime alert %r", self.guild_id, key)
                continue
            alerts[alert.monitor_id] = alert
        return alerts

    @translates_db_errors
    async def save(self, alert: MonitorAlert) -> None:
        await self._col().update_one(
            {"_id": DOC_ID}, {"$set": {str(alert.monitor_id): alert.to_mongo()}}, upsert=True
        )

    @translates_db_errors
    async def remove(self, monitor_id: int) -> None:
        await self._col().update_one({"_id": DOC_ID}, {"$unset": {str(monitor_id): ""}})

    @translates_db_errors
    async def set_last_status(self, monitor_id: int, status: MonitorStatus | None) -> None:
        key = str(monitor_id)
        await self._col().update_one(
            {"_id": DOC_ID, key: {"$exists": True}},
            {"$set": {f"{key}.last_status": int(status) if status is not None else None}},
        )
