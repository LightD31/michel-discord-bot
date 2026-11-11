"""Fold a burst of events into a few runs of the work they all ask for.

A reaction on a giveaway or a poll re-reads every reactor of the message to
re-render it: one re-read per reaction made a busy message cost O(n²) calls to
Discord. :class:`Coalescer` runs the work for a key at most once per *delay*,
with the latest state, however many events ask for it in between.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Hashable

from src.core.tasks import spawn

_logger = logging.getLogger(__name__)

Refresh = Callable[[], Awaitable[object]]


class Coalescer:
    """Run the refresh of a key *delay* seconds after it is asked for, once for a burst.

    The first :meth:`request` for a key schedules a run after *delay*. Requests
    made before it starts fold into it; requests made while it runs schedule
    one more run after it. Runs of a key never overlap, and each runs the
    refresh passed by the latest request (holding the freshest objects).
    """

    def __init__(self, delay: float, *, name: str, log: logging.Logger | None = None) -> None:
        self.delay = delay
        self._name = name
        self._log = log
        # key -> refresh asked for and not started yet (None: running, nothing more asked)
        self._waiting: dict[Hashable, Refresh | None] = {}

    def request(self, key: Hashable, refresh: Refresh) -> None:
        """Ask for *refresh* to run for *key*, soon."""
        running = key in self._waiting
        self._waiting[key] = refresh
        if not running:
            spawn(self._run(key), name=f"{self._name}:{key}", log=self._log)

    def pending(self) -> int:
        """How many keys have a run scheduled or in progress."""
        return len(self._waiting)

    async def _run(self, key: Hashable) -> None:
        try:
            while self._waiting.get(key) is not None:
                await asyncio.sleep(self.delay)
                refresh = self._waiting[key]
                self._waiting[key] = None
                if refresh is None:
                    continue
                try:
                    await refresh()
                except Exception:  # noqa: BLE001 — the next request gets its run
                    (self._log or _logger).exception("%s: refresh of %s failed", self._name, key)
        finally:
            self._waiting.pop(key, None)


__all__ = ["Coalescer"]
