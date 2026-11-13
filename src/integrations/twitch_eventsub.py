"""twitchAPI's EventSub websocket, started and stopped without blocking the caller's loop.

``EventSubWebsocket.start()`` is synchronous: it spawns the socket thread, then
spins on ``time.sleep(0.01)`` until Twitch's welcome message arrives. Called on
the bot loop, that froze the gateway, every command and the heartbeat for as
long as the connection took, and for ever when Twitch was unreachable (the
socket thread dies after ~255 s of retries and nothing ends the spin): the
watchdog then killed the process, and the restart froze again. ``stop()``
blocks the same way on ``Future.result()``.

:class:`BoundedEventSubWebsocket` keeps the library's thread and adds the two
coroutines the bot calls instead, both with a deadline. On a failed or late
start the socket thread is abandoned (its tasks cancelled, its session and loop
closed), so a retry starts from a clean slate.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.type import AuthType, UnauthorizedException

from src.core import logging as logutil

logger = logutil.init_logger(__name__)

START_TIMEOUT_SECONDS = 60.0
STOP_TIMEOUT_SECONDS = 5.0
_POLL_SECONDS = 0.05


class EventSubStartError(ConnectionError):
    """The websocket didn't get Twitch's welcome in time (or its thread died)."""


class BoundedEventSubWebsocket(EventSubWebsocket):
    """``EventSubWebsocket`` with :meth:`start_async` / :meth:`stop_async`.

    Relies on the library's private state (``_running``, ``_startup_complete``,
    ``_socket_thread``, ``_socket_loop``…), pinned by ``uv.lock``; the sync
    ``start()`` / ``stop()`` are left as they are and never called by the bot.
    """

    def _run_socket(self) -> None:
        """The library's thread body, made to end cleanly however it stops."""
        try:
            super()._run_socket()
        except BaseException as e:  # noqa: BLE001 — a thread has nobody to raise to
            if self._running:
                logger.warning("EventSub socket thread stopped: %s", e)
        finally:
            self._close_socket_loop()

    def _close_socket_loop(self) -> None:
        loop = self._socket_loop
        if loop is None or loop.is_closed() or loop.is_running():
            return
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        with contextlib.suppress(Exception):
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        session = self._session
        with contextlib.suppress(Exception):
            if session is not None and not session.closed:
                loop.run_until_complete(session.close())
        with contextlib.suppress(Exception):
            loop.close()

    async def start_async(self, timeout: float = START_TIMEOUT_SECONDS) -> None:
        """Start the socket thread and wait, without blocking, for Twitch's welcome.

        Raises :class:`EventSubStartError` when the thread dies or *timeout*
        passes first; the thread is then abandoned.
        """
        if self._running:
            raise RuntimeError("EventSubWebsocket is already started!")
        if not self._twitch.has_required_auth(AuthType.USER, []):
            raise UnauthorizedException("Twitch needs user authentication")
        self._startup_complete = False
        self._ready = False
        self._closing = False
        self._active_subscriptions = {}
        thread = threading.Thread(target=self._run_socket, name="twitch-eventsub", daemon=True)
        self._socket_thread = thread  # type: ignore[assignment]  # the library types it None
        self._running = True
        thread.start()
        deadline = time.monotonic() + timeout
        while not self._startup_complete:
            if not thread.is_alive():
                self._running = False
                raise EventSubStartError("EventSub socket thread ended before Twitch's welcome")
            if time.monotonic() >= deadline:
                self._abandon()
                raise EventSubStartError(f"no EventSub welcome from Twitch within {timeout:.0f} s")
            await asyncio.sleep(_POLL_SECONDS)

    async def stop_async(self, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
        """Close the websocket within *timeout*, else abandon its thread. Idempotent."""
        if not self._running:
            return
        self._startup_complete = False
        self._running = False
        self._ready = False
        loop = self._socket_loop
        if loop is None or loop.is_closed() or self._connection is None:
            self._abandon()
            return
        future = asyncio.run_coroutine_threadsafe(self._stop(), loop)
        try:
            await asyncio.wait_for(asyncio.wrap_future(future), timeout)
        except Exception as e:  # noqa: BLE001 — a teardown must always finish
            logger.warning("EventSub didn't close cleanly (%s), abandoning its thread", e)
            self._abandon()

    def _abandon(self) -> None:
        """Cancel everything on the socket loop, so its thread unwinds and exits."""
        self._running = False
        self._closing = True
        loop = self._socket_loop
        if loop is None or loop.is_closed():
            return

        def cancel_all() -> None:
            for task in asyncio.all_tasks(loop):
                task.cancel()

        with contextlib.suppress(RuntimeError):  # the loop closed meanwhile
            loop.call_soon_threadsafe(cancel_all)


__all__ = [
    "START_TIMEOUT_SECONDS",
    "STOP_TIMEOUT_SECONDS",
    "BoundedEventSubWebsocket",
    "EventSubStartError",
]
