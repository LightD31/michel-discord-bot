"""Tests for the Web UI log handler's SSE fan-out.

The handler is written to on whichever thread logged the record — the bot
loop's, usually — while its subscriber queues belong to the dashboard's
uvicorn loop in a separate thread. These tests pin down that hand-off.
"""

import asyncio
import logging
import threading

import pytest

from src.webui.log_handler import WebUILogHandler


@pytest.fixture
def handler():
    """A handler that does not leak into the module-level singleton slot."""
    previous = WebUILogHandler._instance
    instance = WebUILogHandler(max_entries=10)
    yield instance
    WebUILogHandler._instance = previous


def make_record(message: str, name: str = "test.logger") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.INFO,
        pathname="/app/test.py",
        lineno=42,
        msg=message,
        args=(),
        exc_info=None,
    )


async def test_entry_emitted_from_another_thread_reaches_the_subscriber(handler):
    """Regression: emit() used to call put_nowait across loops directly.

    asyncio.Queue is not thread-safe, so the awaiting consumer was not
    reliably woken. The entry must now arrive promptly.
    """
    queue = handler.subscribe()

    thread = threading.Thread(target=handler.emit, args=(make_record("from the bot loop"),))
    thread.start()
    thread.join()

    entry = await asyncio.wait_for(queue.get(), timeout=2)
    assert entry.message == "from the bot loop"
    assert entry.logger_name == "test.logger"
    assert entry.lineno == 42


async def test_every_subscriber_receives_the_entry(handler):
    first = handler.subscribe()
    second = handler.subscribe()

    thread = threading.Thread(target=handler.emit, args=(make_record("broadcast"),))
    thread.start()
    thread.join()

    assert (await asyncio.wait_for(first.get(), timeout=2)).message == "broadcast"
    assert (await asyncio.wait_for(second.get(), timeout=2)).message == "broadcast"


async def test_a_slow_consumer_loses_the_oldest_entry_not_the_newest(handler):
    queue = handler.subscribe()
    for i in range(queue.maxsize + 5):
        handler.emit(make_record(f"entry {i}"))
    await asyncio.sleep(0)  # let the scheduled hand-offs run

    assert queue.qsize() == queue.maxsize
    drained = [queue.get_nowait().message for _ in range(queue.maxsize)]
    assert drained[-1] == f"entry {queue.maxsize + 4}", "the newest entry must survive"
    assert "entry 0" not in drained, "the oldest entry is the one dropped"


async def test_unsubscribe_stops_delivery(handler):
    queue = handler.subscribe()
    handler.unsubscribe(queue)
    assert handler.listener_count == 0

    handler.emit(make_record("after unsubscribe"))
    await asyncio.sleep(0)
    assert queue.empty()


async def test_ignored_loggers_are_not_forwarded(handler):
    queue = handler.subscribe()

    handler.emit(make_record("noise", name="uvicorn.access"))
    await asyncio.sleep(0)

    assert queue.empty()
    assert len(handler.buffer) == 0


async def test_records_land_in_the_ring_buffer_for_polling_clients(handler):
    for i in range(15):
        handler.emit(make_record(f"entry {i}"))

    assert len(handler.buffer) == 10, "buffer is capped at max_entries"
    recent = handler.get_recent(count=3)
    assert [e["message"] for e in recent] == ["entry 12", "entry 13", "entry 14"]


def test_a_closed_loop_subscriber_is_pruned(handler):
    """A dashboard thread that died must not keep the handler retrying.

    Synchronous on purpose: it owns and closes its own loop, which cannot be
    done from inside a running one.
    """
    dead_loop = asyncio.new_event_loop()

    async def subscribe_here():
        return handler.subscribe()

    queue = dead_loop.run_until_complete(subscribe_here())
    assert handler.listener_count == 1
    dead_loop.close()

    handler.emit(make_record("after the loop died"))
    assert handler.listener_count == 0
    assert queue.empty()
