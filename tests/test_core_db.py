"""Tests for the per-event-loop motor client management in MongoManager.

Motor pins each client to the event loop that first uses it, so the manager
must hand out a distinct client per loop (bot loop vs. Web UI uvicorn loop)
or cross-loop awaits fail with "got Future attached to a different loop".
No MongoDB server is required: clients are created lazily and never queried.
"""

import asyncio

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
    for client in MongoManager._clients.values():
        client.close()
    MongoManager._clients.clear()
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
