"""A small map whose entries expire, for state held between two interactions.

A command that opens a modal keeps what the member chose until the modal is
submitted. A modal that is never submitted would keep that entry for the life
of the process: :class:`ExpiringMap` forgets it after *ttl* seconds, and drops
every expired entry whenever a new one is stored.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable


class ExpiringMap[V]:
    """Values kept *ttl* seconds after they were stored, then treated as absent."""

    def __init__(self, ttl: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._entries: dict[Hashable, tuple[float, V]] = {}

    def __setitem__(self, key: Hashable, value: V) -> None:
        now = self._clock()
        for stale in [k for k, (stored, _) in self._entries.items() if now - stored >= self._ttl]:
            del self._entries[stale]
        self._entries[key] = (now, value)

    def pop(self, key: Hashable) -> V | None:
        """Remove *key* and return its value, or None when it is absent or expired."""
        entry = self._entries.pop(key, None)
        if entry is None or self._clock() - entry[0] >= self._ttl:
            return None
        return entry[1]

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["ExpiringMap"]
