"""Record every ``interactions`` ``Task`` run, and give it a log context.

``Task.__call__`` runs the callback and hands an exception to ``Task.on_error``
instead of raising, so a task that fails every time looks, from outside, like
one that works. :func:`install` wraps both methods, once for the process:

- each run is timed and recorded in :data:`src.core.health.health` as a
  success, or as a failure when ``on_error`` was called during it, which the
  Prometheus exporter turns into ``michel_task_*`` series;
- each run binds ``task`` and a fresh ``correlation_id`` in the log context,
  so its log lines can be told apart from another run's;
- a failure is sent to the error reporter (:mod:`src.core.error_reporting`)
  when one is configured, since interactions.py logs it without the
  exception attached.

The library's own behaviour (rescheduling, the error log line) is kept: the
originals are called, not reimplemented.
"""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import Any

from interactions import Task

from src.core import error_reporting, log_context
from src.core.health import health

# Set for the duration of one run: on_error flips it so the run counts as failed.
_run_failed: ContextVar[list[bool] | None] = ContextVar("michel_task_run_failed", default=None)
_installed = False


def task_name(task: Task) -> str:
    """``Extension.method`` for a task defined on an extension, the function name otherwise."""
    callback = task.callback
    func = getattr(callback, "func", callback)  # extensions bind tasks with functools.partial
    return str(getattr(func, "__qualname__", None) or getattr(func, "__name__", None) or func)


def install() -> None:
    """Wrap ``Task.__call__`` and ``Task.on_error``; calling it again does nothing."""
    global _installed
    if _installed:
        return
    original_call = Task.__call__
    original_on_error = Task.on_error

    async def call(self: Task, *args: Any, **kwargs: Any) -> None:
        name = task_name(self)
        failed = [False]
        marker = _run_failed.set(failed)
        started = time.monotonic()
        try:
            with log_context.scope(task=name, correlation_id=log_context.new_id()):
                await original_call(self, *args, **kwargs)
        except asyncio.CancelledError:
            raise  # stopped mid-run (unload, shutdown): neither a success nor a failure
        else:
            health.record_task(name, ok=not failed[0], duration=time.monotonic() - started)
        finally:
            _run_failed.reset(marker)

    def on_error(self: Task, error: Exception) -> None:
        failed = _run_failed.get()
        if failed is not None:
            failed[0] = True
        error_reporting.capture(error)
        original_on_error(self, error)

    Task.__call__ = call  # type: ignore[method-assign]
    Task.on_error = on_error  # type: ignore[method-assign]
    _installed = True


__all__ = ["install", "task_name"]
