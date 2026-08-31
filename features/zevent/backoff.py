"""Retry pacing for the community stats API.

The EvenMoreStats backend behind zevent.gdoc.fr is a community project on
modest hardware, so the tracker must stay a light client — and above all must
not retry harder when it starts failing. Each cached fetch owns a
:class:`RetryGate`: it stamps *every* attempt rather than only the successful
ones, so an outage widens the interval instead of collapsing it to the refresh
loop's own cadence.
"""

from __future__ import annotations

from datetime import datetime, timedelta


class RetryGate:
    """Decide when a periodically refreshed fetch may run again.

    After a success the next attempt waits ``interval``. After a failure it
    waits ``retry_delay``, doubling on each consecutive failure up to
    ``max_retry_delay`` — so a blip recovers within a minute while a sustained
    outage settles to an occasional probe.
    """

    def __init__(
        self,
        interval: timedelta,
        retry_delay: timedelta = timedelta(minutes=1),
        max_retry_delay: timedelta = timedelta(minutes=30),
    ) -> None:
        self.interval = interval
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        self._next_attempt: datetime | None = None
        self._failures = 0

    @property
    def failures(self) -> int:
        """Consecutive failures since the last success."""
        return self._failures

    def ready(self, now: datetime) -> bool:
        """True when the caller may attempt the fetch again."""
        return self._next_attempt is None or now >= self._next_attempt

    def succeeded(self, now: datetime) -> None:
        self._failures = 0
        self._next_attempt = now + self.interval

    def failed(self, now: datetime) -> None:
        """Record a failed attempt and back the next one off exponentially."""
        delay = self.retry_delay * (2**self._failures)
        self._failures += 1
        self._next_attempt = now + min(delay, self.max_retry_delay)
