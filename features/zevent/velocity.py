"""Track how fast each streamer's donation total is moving.

The community stats API exposes no history: goals carry only an
``accomplished`` boolean, and every timeline-shaped endpoint 404s. So the rate
has to be measured client-side — which costs nothing, because ``zevent.fr``
already reports a per-streamer donation total and the refresh loop already
fetches it every cycle.

The point is to notice a goal being *pushed* over: when a chat piles onto one
streamer, the rate spikes and the estimated time to the next goal collapses
from hours to minutes. Ranking on the raw rate would just re-elect the biggest
earners all event; ranking on the ETA surfaces the goal that is about to fall.

Pure bookkeeping — no I/O, no clock of its own; the caller supplies ``now``.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

DEFAULT_WINDOW = timedelta(minutes=5)


class DonationVelocity:
    """Rolling per-streamer donation rate, measured over a sliding window.

    A single 30-second delta is far too noisy to rank on — one large donation
    would read as a permanent surge. Averaging across a few minutes smooths
    that out while still reacting inside the span of a raid.
    """

    def __init__(self, window: timedelta = DEFAULT_WINDOW) -> None:
        self.window = window
        self._samples: dict[str, deque[tuple[datetime, float]]] = {}

    def record(self, amounts: dict[str, float], now: datetime) -> None:
        """Add one observation of ``{twitch_login: total raised}``."""
        for login, amount in amounts.items():
            series = self._samples.setdefault(login, deque())
            # Totals only ever grow; a drop means the source reset or the
            # payload was partial, so restart rather than infer a huge rate.
            if series and amount < series[-1][1]:
                series.clear()
            series.append((now, float(amount)))
            cutoff = now - self.window
            while len(series) > 2 and series[1][0] < cutoff:
                series.popleft()

        # Drop streamers absent from this observation so the map cannot grow
        # without bound across a long event.
        for login in self._samples.keys() - amounts.keys():
            del self._samples[login]

    def rate_per_minute(self, login: str) -> float | None:
        """Euros per minute over the window, or ``None`` without two samples."""
        series = self._samples.get(login)
        if not series or len(series) < 2:
            return None
        (start, first), (end, last) = series[0], series[-1]
        elapsed = (end - start).total_seconds() / 60
        if elapsed <= 0:
            return None
        gained = last - first
        return gained / elapsed if gained > 0 else 0.0

    def eta_minutes(self, login: str, remaining: float) -> float | None:
        """Minutes until ``remaining`` euros are raised at the current rate.

        ``None`` when nothing is moving or the rate is unknown — an unbounded
        wait, not an urgent one.
        """
        if remaining <= 0:
            return 0.0
        rate = self.rate_per_minute(login)
        if not rate:
            return None
        return remaining / rate

    def tracked(self) -> int:
        """How many streamers currently have samples (for logging)."""
        return len(self._samples)
