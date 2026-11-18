"""Limits on the anonymous messages between a Santa and their recipient.

Nothing a member writes through the relay is stored: the bot reads the modal,
sends the DM and forgets it. What limits abuse is kept in memory only, per
sender and direction, so a restart forgets it too (at worst one extra burst).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

# Discord's modal paragraph allows 4000; a note to a Santa needs far less.
MAX_MESSAGE_LENGTH = 1000
BURST = 5
BURST_WINDOW = timedelta(minutes=10)
PER_DAY = 20

TOO_MANY = (
    "Doucement ! Tu as envoyé beaucoup de messages : réessaie {when}. "
    "(Au plus 5 messages en 10 minutes et 20 par jour.)"
)


class RelayLimiter:
    """At most :data:`BURST` messages per :data:`BURST_WINDOW` and :data:`PER_DAY` a day."""

    def __init__(self) -> None:
        self._sent: dict[tuple[str, int, str], deque[datetime]] = {}

    def retry_at(self, key: tuple[str, int, str], now: datetime) -> datetime | None:
        """``None`` when *key* may send at *now*, else when it may again."""
        sent = self._sent.get(key)
        if not sent:
            return None
        while sent and now - sent[0] >= timedelta(days=1):
            sent.popleft()
        if len(sent) >= PER_DAY:
            return sent[0] + timedelta(days=1)
        recent = [moment for moment in sent if now - moment < BURST_WINDOW]
        if len(recent) >= BURST:
            return recent[0] + BURST_WINDOW
        return None

    def record(self, key: tuple[str, int, str], now: datetime) -> None:
        self._sent.setdefault(key, deque()).append(now)

    def forget(self, context_id: str) -> None:
        """Drop a session's counters (cancelled or replaced)."""
        for key in [k for k in self._sent if k[0] == context_id]:
            del self._sent[key]


__all__ = ["BURST", "MAX_MESSAGE_LENGTH", "PER_DAY", "TOO_MANY", "RelayLimiter"]
