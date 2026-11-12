"""Tests for the per-event-loop client management in MongoManager.

An ``AsyncMongoClient`` belongs to the event loop that first uses it, so the manager
must hand out a distinct client per loop (bot loop vs. Web UI uvicorn loop)
or cross-loop awaits fail with "got Future attached to a different loop".
No MongoDB server is required: clients are created lazily and never queried.
"""

import asyncio
import threading

import pytest

from src.core.db import MongoManager, mongo_manager


@pytest.fixture
def isolated_manager():
    """Point the singleton at a dummy URL and restore its state afterwards."""
    saved_clients = dict(MongoManager._clients)
    saved_url = mongo_manager._url
    MongoManager._clients.clear()
    mongo_manager._url = "mongodb://localhost:27017"
    yield mongo_manager
    MongoManager._clients.clear()  # never connected: their loops are gone, nothing to close
    MongoManager._clients.update(saved_clients)
    mongo_manager._url = saved_url


def test_same_loop_reuses_client(isolated_manager):
    async def grab_twice():
        return isolated_manager._ensure_client(), isolated_manager._ensure_client()

    first, second = asyncio.run(grab_twice())
    assert first is second


def test_distinct_loops_get_distinct_clients(isolated_manager):
    async def grab():
        return isolated_manager._ensure_client()

    # asyncio.run creates a fresh event loop each call.
    first = asyncio.run(grab())
    second = asyncio.run(grab())
    assert first is not second
    assert len(MongoManager._clients) == 2


def test_close_clears_all_clients(isolated_manager):
    async def grab():
        return isolated_manager._ensure_client()

    asyncio.run(grab())
    asyncio.run(grab())
    asyncio.run(isolated_manager.close())
    assert not MongoManager._clients


class RecordingClient:
    """Stands in for an ``AsyncMongoClient``: records the loop it was closed on."""

    def __init__(self):
        self.closed_on = None

    async def close(self):
        self.closed_on = asyncio.get_running_loop()


def test_each_client_is_closed_on_its_own_loop(isolated_manager):
    other = asyncio.new_event_loop()
    thread = threading.Thread(target=other.run_forever, daemon=True)
    thread.start()
    try:
        webui_client, stopped_client = RecordingClient(), RecordingClient()
        stopped = asyncio.new_event_loop()
        stopped.close()
        MongoManager._clients[other] = webui_client
        MongoManager._clients[stopped] = stopped_client

        async def close_from_the_bot_loop():
            bot_client = RecordingClient()
            MongoManager._clients[asyncio.get_running_loop()] = bot_client
            await isolated_manager.close()
            return bot_client, asyncio.get_running_loop()

        bot_client, bot_loop = asyncio.run(close_from_the_bot_loop())
    finally:
        other.call_soon_threadsafe(other.stop)
        thread.join(5)
        other.close()
    assert bot_client.closed_on is bot_loop
    assert webui_client.closed_on is other
    assert stopped_client.closed_on is None  # its loop is gone: only dropped
    assert not MongoManager._clients
