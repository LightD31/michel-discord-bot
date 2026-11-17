"""Shared fixtures.

``mongo`` runs a test against a real MongoDB server, for repository tests whose
queries (aggregations, atomic updates, TTL and unique indexes) an in-memory fake
can't check. Their modules carry ``pytestmark = pytest.mark.mongo``. Set
``MONGO_TEST_URL`` to enable them; without it they're skipped::

    docker run -d --rm -p 27017:27017 mongo:8
    MONGO_TEST_URL=mongodb://localhost:27017 uv run pytest -m mongo

CI's ``test`` job runs them against a ``mongo`` service container.

Every test runs away from the developer's own setup (T03): the config store
reads a ``config.json`` in a temporary directory that doesn't exist (an empty
config, as in CI) instead of ``config/config.json``, and MongoDB has no URL,
so a test that forgets ``store_config`` or ``mongo`` can neither read the real
settings nor reach the real database.
"""

from __future__ import annotations

import os
import random
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from src.core import config as core_config
from src.core import db

MONGO_TEST_URL = os.environ.get("MONGO_TEST_URL", "")

ISOLATED_CONFIG_PATH = os.path.join(tempfile.mkdtemp(prefix="michel-tests-"), "config.json")
core_config.CONFIG_PATH = ISOLATED_CONFIG_PATH
core_config.config_store._data = None
db.mongo_manager._url = ""  # not None: None would read mongodb.url from the config


@pytest.fixture(autouse=True)
def _isolated_from_the_real_setup(monkeypatch):
    """Put the isolation back for each test, whatever the previous one left."""
    monkeypatch.setattr(core_config, "CONFIG_PATH", ISOLATED_CONFIG_PATH)
    monkeypatch.setattr(db.mongo_manager, "_url", "")
    yield
    if os.path.exists(ISOLATED_CONFIG_PATH):  # a test saved without its own path
        os.remove(ISOLATED_CONFIG_PATH)
        core_config.config_store._data = None


@dataclass
class MongoTestEnv:
    """A throwaway guild database (and global database) on the test server."""

    guild_id: str
    manager: db.MongoManager

    def guild_collection(self, name: str):
        return self.manager.get_guild_collection(self.guild_id, name)


@pytest.fixture
async def mongo(monkeypatch) -> AsyncIterator[MongoTestEnv]:
    """Point ``mongo_manager`` at the test server, on databases no one else uses.

    Each test gets a random guild id and its own ``global`` database, both
    dropped afterwards, so tests can run in parallel against one server and
    never touch real data. The client is created on the test's own loop
    and closed at the end.
    """
    if not MONGO_TEST_URL:
        pytest.skip("MONGO_TEST_URL is not set")
    manager = db.mongo_manager
    monkeypatch.setattr(manager, "_url", MONGO_TEST_URL)
    monkeypatch.setattr(db.MongoManager, "_clients", {})
    monkeypatch.setattr(db, "GLOBAL_DB_NAME", f"test_global_{uuid.uuid4().hex[:12]}")
    # A leading zero: no real Discord id looks like this, so even a URL pointing
    # at the bot's own server can't make teardown drop a real guild's database.
    guild_id = f"0{random.randint(10**16, 10**17 - 1)}"
    env = MongoTestEnv(guild_id=guild_id, manager=manager)
    try:
        yield env
    finally:
        client = manager.client
        await client.drop_database(f"{db.GUILD_DB_PREFIX}{guild_id}")
        await client.drop_database(db.GLOBAL_DB_NAME)
        await manager.close()


@pytest.fixture
def store_config():
    """Make the config store serve a given dict for the test, then restore it.

    ``ModuleConfig`` and ``global_config`` read the store, so this is how a
    test hands an extension its configuration::

        store_config({"servers": {"42": {"moduleXp": {"enabled": True}}}})
    """
    from src.core.config import config_store

    saved = config_store._data

    def serve(data: dict) -> dict:
        config_store._data = data
        return data

    yield serve
    config_store._data = saved
