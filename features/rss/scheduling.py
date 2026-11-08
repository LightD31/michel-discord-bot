"""When each feed is polled next: its guild's interval, stretched after failures.

A feed that keeps failing (site down, URL gone, malformed XML) is retried
less and less often: its interval doubles with each consecutive failure, up to
:data:`BACKOFF_CAP`, and the first successful poll brings it back to normal.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from features.rss.models import RssFeedState

#: Longest wait between two attempts on a failing feed. A guild interval above
#: it is kept as is: the backoff never polls a feed more often than configured.
BACKOFF_CAP = timedelta(hours=6)


def poll_delay(interval: timedelta, failures: int) -> timedelta:
    """The wait before the next poll: *interval*, doubled per consecutive failure."""
    if failures <= 0:
        return interval
    stretched = interval * (2 ** min(failures, 16))
    return max(interval, min(stretched, BACKOFF_CAP))


def next_poll_at(state: RssFeedState | None, interval: timedelta) -> datetime | None:
    """When the feed is next due, ``None`` when it was never polled."""
    if state is None or state.last_poll_at is None:
        return None
    return state.last_poll_at + poll_delay(interval, state.failures)


def is_due(state: RssFeedState | None, interval: timedelta, now: datetime) -> bool:
    """Whether the feed should be fetched at *now*."""
    due = next_poll_at(state, interval)
    return due is None or now >= due


__all__ = ["BACKOFF_CAP", "is_due", "next_poll_at", "poll_delay"]
