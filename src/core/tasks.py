"""Fire-and-forget background task helper.

``asyncio.create_task`` hands back a task the event loop only holds *weakly*.
A caller that discards the handle — ``asyncio.create_task(self.run())`` — lets
the garbage collector reclaim the task mid-await, which cancels it silently:
no traceback, no log line, just work that stopped happening.

Anything started outside an ``interactions`` ``@Task.create`` trigger goes
through :func:`spawn`, which keeps the handle alive until the coroutine
finishes and reports a failure instead of dropping it on the floor.

Usage::

    from src.core.tasks import spawn

    spawn(self.run(), name="twitch-eventsub", logger=logger)
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine
from typing import Any

from src.core import logging as _logging

logger = _logging.init_logger(os.path.basename(__file__))

# Strong references to in-flight tasks, discarded as each one completes. This
# set is the whole point of the module: without it the loop's weak reference is
# the only one, and the task becomes collectable.
_background_tasks: set[asyncio.Task[Any]] = set()


def spawn(
    coro: Coroutine[Any, Any, Any],
    *,
    name: str,
    log: logging.Logger | None = None,
) -> asyncio.Task[Any]:
    """Schedule *coro* on the running loop and keep it alive until it finishes.

    Parameters
    ----------
    name:
        Identifies the task in tracebacks and in the failure log line.
    log:
        Logger to report an unhandled exception on. Defaults to this module's.

    Returns the task, so callers that want to await or cancel it still can.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(lambda finished: _on_done(finished, name, log))
    return task


def _on_done(task: asyncio.Task[Any], name: str, log: logging.Logger | None) -> None:
    """Drop the strong reference and surface whatever the task raised."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    # Always retrieve the exception, even with no logger, so the loop does not
    # report it later as "Task exception was never retrieved".
    error = task.exception()
    if error is not None:
        (log or logger).error("Background task %s failed: %s", name, error, exc_info=error)


def pending_task_count() -> int:
    """Number of tasks currently held alive (diagnostics and tests)."""
    return len(_background_tasks)


__all__ = ["pending_task_count", "spawn"]
