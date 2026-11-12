"""The bot's clock: instants in UTC, wall times in the community's time zone.

Every instant the bot keeps (a deadline, a creation date) is a timezone-aware
UTC ``datetime``; MongoDB returns them that way too (``tz_aware`` client in
:mod:`src.core.db`). Wall times are what members type and what daily
schedules mean ("20:00", "every day at 9"): they are read in :data:`LOCAL_TZ`
and turned into UTC instants with :func:`at_local`. Calendar steps (the next
day, week or month of a recurring reminder) are taken in local time too, so
a 20:00 reminder stays at 20:00 across a daylight saving change.

``tests/test_datetimes.py`` refuses naive ``datetime.now()`` / ``utcnow()``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# The time zone of the community: hours members type, daily schedules, and
# dates shown as text. The ``tzdata`` package ships the database, so it works
# on an image without /usr/share/zoneinfo.
LOCAL_TZ = ZoneInfo("Europe/Paris")


def now() -> datetime:
    """The current instant, in UTC."""
    return datetime.now(UTC)


def local_now() -> datetime:
    """The current instant, on the community's wall clock."""
    return datetime.now(LOCAL_TZ)


def to_local(moment: datetime) -> datetime:
    """*moment* on the community's wall clock (a naive one is taken as UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(LOCAL_TZ)


def as_utc(moment: datetime) -> datetime:
    """*moment* in UTC (a naive one is taken as UTC already)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def at_local(day: date, hour: int = 0, minute: int = 0) -> datetime:
    """The UTC instant of *day* at *hour*:*minute* on the community's wall clock.

    A wall time skipped by the spring change (02:30 that night) lands on the
    same moment an hour later; one repeated by the autumn change is its first
    occurrence.
    """
    # fold=0: a skipped time is read with the offset before the change, so it
    # converts to the instant one gap later; a repeated one is its first pass.
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def local_step(moment: datetime, step: timedelta) -> datetime:
    """*moment* plus *step* of wall-clock time, as a UTC instant (``+1 day`` keeps 20:00)."""
    wall = to_local(moment).replace(tzinfo=None) + step
    return wall.replace(tzinfo=LOCAL_TZ).astimezone(UTC)


__all__ = ["LOCAL_TZ", "as_utc", "at_local", "local_now", "local_step", "now", "to_local"]
