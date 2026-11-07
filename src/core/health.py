"""Process health: what the Prometheus exporter reports beyond the domain statistics.

The bot loop records here as things happen:
- every ``interactions`` ``Task`` run, by ``src.discord_ext.task_health``;
- every request through the shared HTTP sessions (``src.core.http``);
- every :func:`src.core.tasks.spawn` task that died with an exception;
- every finished backup (``MongoManager.backup_all``).

``extensions/metrics.py`` reads it from ``prometheus_client``'s scrape thread,
hence the lock: :meth:`HealthRegistry.snapshot` hands back copies.
:func:`watch_loop_lag` measures how late the event loop wakes up.

Nothing here knows about Discord or Prometheus, so it works (and is tested)
whether the exporter is enabled or not.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

LOOP_LAG_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class TaskRecord:
    """Outcome history of one periodic task."""

    successes: int = 0
    failures: int = 0
    last_success: float | None = None  # epoch seconds
    last_failure: float | None = None
    last_duration: float | None = None  # seconds, last run whatever its outcome


@dataclass
class HealthSnapshot:
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    http_requests: dict[tuple[str, str], int] = field(default_factory=dict)  # (host, outcome)
    background_failures: dict[str, int] = field(default_factory=dict)
    last_backup: float | None = None  # epoch seconds


def http_outcome(status: int | None) -> str:
    """``2xx`` … ``5xx`` for a response, ``error`` when none arrived (timeout, refused…)."""
    if status is None or not 100 <= status <= 599:
        return "error"
    return f"{status // 100}xx"


class HealthRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskRecord] = {}
        self._http: Counter[tuple[str, str]] = Counter()
        self._background: Counter[str] = Counter()
        self._last_backup: float | None = None

    def record_task(self, name: str, *, ok: bool, duration: float, at: float | None = None) -> None:
        now = time.time() if at is None else at
        with self._lock:
            record = self._tasks.get(name, TaskRecord())
            if ok:
                record = replace(
                    record, successes=record.successes + 1, last_success=now, last_duration=duration
                )
            else:
                record = replace(
                    record, failures=record.failures + 1, last_failure=now, last_duration=duration
                )
            self._tasks[name] = record

    def record_http(self, host: str, status: int | None) -> None:
        with self._lock:
            self._http[(host or "unknown", http_outcome(status))] += 1

    def record_background_failure(self, name: str) -> None:
        with self._lock:
            self._background[name] += 1

    def record_backup(self, at: float | None = None) -> None:
        """A backup finished. Keeps the most recent time, so a stale one never wins."""
        when = time.time() if at is None else at
        with self._lock:
            if self._last_backup is None or when > self._last_backup:
                self._last_backup = when

    def snapshot(self) -> HealthSnapshot:
        with self._lock:
            return HealthSnapshot(
                tasks=dict(self._tasks),
                http_requests=dict(self._http),
                background_failures=dict(self._background),
                last_backup=self._last_backup,
            )

    def clear(self) -> None:
        """Forget everything (tests)."""
        with self._lock:
            self._tasks.clear()
            self._http.clear()
            self._background.clear()
            self._last_backup = None


health = HealthRegistry()


async def watch_loop_lag(
    observe: Callable[[float], None], interval: float = LOOP_LAG_INTERVAL_SECONDS
) -> None:
    """Report, every *interval*, how much later than asked the loop woke up.

    A loop blocked by synchronous work (a blocking SDK call, a heavy image
    render…) shows up here long before the watchdog's 180 s heartbeat limit.
    Runs until cancelled.
    """
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        await asyncio.sleep(interval)
        observe(max(loop.time() - started - interval, 0.0))


__all__ = [
    "HealthRegistry",
    "HealthSnapshot",
    "TaskRecord",
    "health",
    "http_outcome",
    "watch_loop_lag",
]
