"""MongoDB I/O for the Uptime feature: the last status the bot saw of each monitor.

The alerts themselves are settings (``moduleUptime.alerts``, P42); what the
bot keeps is the last *settled* status of each monitor, so a restart neither
posts again nor misses a change::

    {"_id": "statuses", "12": 1}
"""

from __future__ import annotations

from src.core.db import GuildRepository, translates_db_errors
from src.core.logging import init_logger
from src.integrations.uptime_kuma import MonitorStatus

logger = init_logger(__name__)

COLLECTION = "uptime_monitors"
DOC_ID = "statuses"


class UptimeRepository(GuildRepository):
    """Accessor for one guild's monitor statuses."""

    COLLECTION = COLLECTION

    @translates_db_errors
    async def load_statuses(self) -> dict[int, MonitorStatus | None]:
        doc = await self._col().find_one({"_id": DOC_ID})
        statuses: dict[int, MonitorStatus | None] = {}
        for key, value in (doc or {}).items():
            if key == "_id":
                continue
            try:
                statuses[int(key)] = MonitorStatus.parse(value)
            except ValueError:
                logger.warning("Guild %s: ignoring malformed uptime status %r", self.guild_id, key)
        return statuses

    @translates_db_errors
    async def set_last_status(self, monitor_id: int, status: MonitorStatus | None) -> None:
        await self._col().update_one(
            {"_id": DOC_ID},
            {"$set": {str(monitor_id): int(status) if status is not None else None}},
            upsert=True,
        )

    @translates_db_errors
    async def forget(self, monitor_id: int) -> None:
        """Drop a monitor's status when its alert is removed (a new one starts afresh)."""
        await self._col().update_one({"_id": DOC_ID}, {"$unset": {str(monitor_id): ""}})
