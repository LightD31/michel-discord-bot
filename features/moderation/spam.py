"""Anti-spam bookkeeping: who sent how many messages in the last few seconds.

One sliding window of timestamps per (guild, member). A member who wrote once
and left must not keep an entry for the life of the process, so windows that
have gone quiet are swept away, at most once a minute.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Hashable

# How often quiet windows are dropped, in seconds.
SWEEP_INTERVAL_SECONDS = 60.0


class SpamTracker:
    """Count each member's recent messages and say when they cross a threshold."""

    def __init__(self) -> None:
        self._windows: dict[Hashable, deque[float]] = {}
        # The longest window asked for since the last sweep: anything quieter
        # than that is gone from every guild's window.
        self._longest_window = 0.0
        self._last_sweep = 0.0

    def hit(self, key: Hashable, now: float, *, threshold: int, window: float) -> bool:
        """Record a message from *key* at *now*; True when it makes *threshold* in *window* s.

        A key that triggers starts over, so the next messages don't trigger again
        one by one.
        """
        self._sweep(now, window)
        stamps = self._windows.setdefault(key, deque())
        stamps.append(now)
        while stamps and now - stamps[0] > window:
            stamps.popleft()
        if len(stamps) >= threshold:
            del self._windows[key]
            return True
        return False

    def _sweep(self, now: float, window: float) -> None:
        self._longest_window = max(self._longest_window, window)
        if now - self._last_sweep < SWEEP_INTERVAL_SECONDS:
            return
        horizon = self._longest_window
        for key in [k for k, stamps in self._windows.items() if now - stamps[-1] > horizon]:
            del self._windows[key]
        self._last_sweep = now
        self._longest_window = window

    def __len__(self) -> int:
        return len(self._windows)


__all__ = ["SWEEP_INTERVAL_SECONDS", "SpamTracker"]
