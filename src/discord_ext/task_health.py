"""Record every ``interactions`` ``Task`` run, give it a log context, never overlap it.

``Task.__call__`` runs the callback and hands an exception to ``Task.on_error``
instead of raising, so a task that fails every time looks, from outside, like
one that works. And the scheduler fires each run as a new asyncio task without
waiting for the previous one, so a run longer than the interval overlaps the
next (two giveaway draws, the same reminder sent twice). :func:`install` wraps
``Task.__call__``, ``Task.on_error`` and ``Task._fire``, once for the process:

- each run is timed and recorded in :data:`src.core.health.health` as a
  success, or as a failure when ``on_error`` was called during it, which the
  Prometheus exporter turns into ``michel_task_*`` series;
- each run binds ``task`` and a fresh ``correlation_id`` in the log context,
  so its log lines can be told apart from another run's;
- a failure is sent to the error reporter (:mod:`src.core.error_reporting`)
  when one is configured, since interactions.py logs it without the
  exception attached;
- a scheduled firing is skipped, logged and counted as ``skipped`` while a
  previous run of the same task (scheduled or called directly) is still going.

The library's own behaviour (rescheduling, the error log line) is kept: the
originals are called, not reimplemented.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from interactions import Task

from src.core import error_reporting, log_context
from src.core import logging as logutil
from src.core.health import health

logger = logutil.init_logger(__name__)

# Set for the duration of one run: on_error flips it so the run counts as failed.
_run_failed: ContextVar[list[bool] | None] = ContextVar("michel_task_run_failed", default=None)
_installed = False
# Runs in progress, per Task (by id: the Task is alive for as long as it runs).
_in_flight: Counter[int] = Counter()


def is_running(task: Task) -> bool:
    """Whether a run of *task* is in progress."""
    return _in_flight[id(task)] > 0


def task_name(task: Task) -> str:
    """``Extension.method`` for a task defined on an extension, the function name otherwise."""
    callback = task.callback
    func = getattr(callback, "func", callback)  # extensions bind tasks with functools.partial
    return str(getattr(func, "__qualname__", None) or getattr(func, "__name__", None) or func)


def install() -> None:
    """Wrap ``Task.__call__``, ``Task.on_error`` and ``Task._fire``; calling it again does nothing."""
    global _installed
    if _installed:
        return
    original_call = Task.__call__
    original_on_error = Task.on_error
    original_fire = Task._fire

    async def call(self: Task, *args: Any, **kwargs: Any) -> None:
        name = task_name(self)
        failed = [False]
        marker = _run_failed.set(failed)
        started = time.monotonic()
        _in_flight[id(self)] += 1
        try:
            with log_context.scope(task=name, correlation_id=log_context.new_id()):
                await original_call(self, *args, **kwargs)
        except asyncio.CancelledError:
            raise  # stopped mid-run (unload, shutdown): neither a success nor a failure
        else:
            health.record_task(name, ok=not failed[0], duration=time.monotonic() - started)
        finally:
            _in_flight[id(self)] -= 1
            if not _in_flight[id(self)]:
                del _in_flight[id(self)]
            _run_failed.reset(marker)

    def on_error(self: Task, error: Exception) -> None:
        failed = _run_failed.get()
        if failed is not None:
            failed[0] = True
        error_reporting.capture(error)
        original_on_error(self, error)

    def fire(self: Task, fire_time: datetime, *args: Any, **kwargs: Any) -> asyncio.Future[Any]:
        if not is_running(self):
            return original_fire(self, fire_time, *args, **kwargs)
        # The trigger computes the next firing from this one: record it, or the
        # scheduler would fire again at once.
        self.trigger.set_last_call_time(fire_time)
        name = task_name(self)
        health.record_task_skipped(name)
        logger.warning("%s is still running: this run is skipped", name)
        skipped: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        skipped.set_result(None)
        return skipped

    Task.__call__ = call  # type: ignore[method-assign]
    Task.on_error = on_error  # type: ignore[method-assign]
    Task._fire = fire  # type: ignore[method-assign,assignment]
    _installed = True


__all__ = ["install", "is_running", "task_name"]
