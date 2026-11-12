"""Releasing what an extension holds: its ``Task`` loops and live connections.

Shared by the dashboard's unload/reload (``src.webui.botops``) and by the
process shutdown (:class:`GracefulShutdown`), so an extension frees its
resources the same way whichever of the two ends it.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from interactions import Task

from src.core import logging as logutil
from src.core.logging import log_failure
from src.core.tasks import spawn

logger = logutil.init_logger("discord_ext.lifecycle")

# Budget for one extension's ``async_drop()`` (closing a websocket…).
ASYNC_DROP_TIMEOUT_SECONDS = 15.0
# Docker waits 10 s after SIGTERM before SIGKILL: finish well inside that.
SHUTDOWN_TIMEOUT_SECONDS = 7.0
# After a clean shutdown, how long main.py gets to exit before we do it here.
GATEWAY_EXIT_GRACE_SECONDS = 2.0
SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)


def stop_tasks(ext: Any) -> list[str]:
    """Stop every ``Task`` defined on ``ext`` (its class and mixins)."""
    stopped = []
    for name, task in inspect.getmembers(type(ext), lambda v: isinstance(v, Task)):
        if task.running:
            stopped.append(name)
        task.stop()
    return stopped


async def release(ext: Any, *, timeout: float = ASYNC_DROP_TIMEOUT_SECONDS) -> list[str]:
    """Stop ``ext``'s tasks and await its optional ``async_drop()``; return the stopped tasks.

    A failing or slow ``async_drop()`` is logged, never raised: cleanup must not
    block an unload or a shutdown.
    """
    stopped = stop_tasks(ext)
    async_drop = getattr(ext, "async_drop", None)
    if async_drop is not None:
        name = getattr(ext, "extension_name", type(ext).__name__)
        try:
            await asyncio.wait_for(async_drop(), timeout=timeout)
        except Exception as e:  # noqa: BLE001 — never block on cleanup
            log_failure(logger, "async_drop() failed for %s: %s", name, e)
    return stopped


class GracefulShutdown:
    """Turns SIGTERM / SIGINT into an orderly stop of the bot.

    Without it, ``docker compose stop`` kills the process mid-flight: task loops
    die wherever they are and open connections are dropped. On a signal this
    releases every loaded extension, runs *closers* (the shared HTTP sessions,
    the MongoDB clients…), stops the gateway connection and lets ``main.py``
    exit with status 0. If that takes longer than :data:`SHUTDOWN_TIMEOUT_SECONDS`,
    the process exits anyway.
    """

    def __init__(
        self,
        client: Any,
        closers: list[Callable[[], Awaitable[Any]]],
        *,
        exit_process: Callable[[int], None] = os._exit,
    ) -> None:
        self.client = client
        self.closers = closers
        self.requested = False
        self._exit = exit_process

    def install(self, loop: asyncio.AbstractEventLoop) -> None:
        """Route the shutdown signals to :meth:`request` (call from the bot loop)."""
        for sig in SHUTDOWN_SIGNALS:
            loop.add_signal_handler(sig, self.request, sig)

    def request(self, sig: signal.Signals | None = None) -> None:
        if self.requested:
            return
        self.requested = True
        logger.info("Received %s — shutting down", sig.name if sig else "a stop request")
        spawn(self._run(), name="graceful-shutdown", log=logger)

    async def _run(self) -> None:
        try:
            await asyncio.wait_for(self.shutdown(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.error("Shutdown took over %.0fs — exiting anyway", SHUTDOWN_TIMEOUT_SECONDS)
            logging.shutdown()
            self._exit(0)
            return
        # Normally the stopped gateway hands control back to main.py, which
        # exits and cancels this task. If it doesn't, don't wait for SIGKILL.
        await asyncio.sleep(GATEWAY_EXIT_GRACE_SECONDS)
        logger.warning("Gateway did not return after stop() — exiting")
        logging.shutdown()
        self._exit(0)

    async def shutdown(self) -> None:
        """Release the extensions, run the closers, then stop the gateway."""
        budget = SHUTDOWN_TIMEOUT_SECONDS / 2
        for ext in list(getattr(self.client, "ext", {}).values()):
            await release(ext, timeout=budget)
        for close in self.closers:
            try:
                await close()
            except Exception as e:  # noqa: BLE001 — keep closing the rest
                log_failure(logger, "Error while closing during shutdown: %s", e)
        await self.client.stop()


__all__ = [
    "ASYNC_DROP_TIMEOUT_SECONDS",
    "SHUTDOWN_TIMEOUT_SECONDS",
    "GracefulShutdown",
    "release",
    "stop_tasks",
]
