"""What the browser tests share (P27): a port, the app served on a thread, a logged-in page.

The browser itself is the ``browser`` fixture of ``conftest.py``, launched once
per module. ``serve()`` runs the dashboard on a local port with uvicorn and,
given the bot loop the app was built with, stops and closes that loop when it
is done: an unclosed loop is a ``ResourceWarning``, which the suite's
``filterwarnings = error`` turns into a failure of whichever test the garbage
collector interrupts.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.webui.context import COOKIE_NAME


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def script_error(message: Any) -> bool:
    """A console error from the app: a module that didn't load, an unknown action.

    A failed request (an API answering 503 on purpose) is logged too, as
    "Failed to load resource": the tests assert on what the page shows instead.
    """
    return message.type == "error" and not message.text.startswith("Failed to load resource")


@contextmanager
def bot_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """An event loop running on its own thread, as the bot's runs beside the dashboard."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True, name="test-bot")
    thread.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


@contextmanager
def serve(app: Any, port: int) -> Iterator[str]:
    """Serve *app* on 127.0.0.1:*port* until the block ends; yields its base URL."""
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True, name="test-web")
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.025)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def logged_in_page(browser: Any, base: str, session_token: str | None) -> Iterator[Any]:
    """A page whose cookie is *session_token* (``None``: a visitor), failing on a script error.

    The app asks with its own dialog: a native one (``confirm()``) is an error too.
    """
    context = browser.new_context()
    if session_token:
        context.add_cookies([{"name": COOKIE_NAME, "value": session_token, "url": base}])
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: script_error(m) and errors.append(m.text))
    page.on("dialog", lambda d: errors.append(f"boîte native : {d.message}") or d.dismiss())
    try:
        yield page
    finally:
        context.close()
    assert errors == [], errors  # no uncaught script error along the way
