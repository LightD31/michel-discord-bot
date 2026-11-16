"""The purge schedule follows the guilds the bot is in (O01)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import extensions.datapurge as datapurge


class Repo:
    def __init__(self):
        self.pending: dict[str, datetime] = {}
        self.purged: list[str] = []

    async def schedule(self, guild_id, left_at):
        self.pending.setdefault(guild_id, left_at)

    async def cancel(self, guild_ids):
        found = [g for g in guild_ids if g in self.pending]
        for g in found:
            del self.pending[g]
        return len(found)

    async def due(self, now):
        return [g for g, at in self.pending.items() if at + timedelta(days=30) <= now]

    async def purge(self, guild_id):
        self.purged.append(guild_id)
        self.pending.pop(guild_id, None)


class Idle:
    running = True


@pytest.fixture
def ext(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleXp": {}}, "2": {"moduleXp": {}}}})
    monkeypatch.setattr(datapurge.mongo_manager, "guild_ids_with_data", _stored(["1", "2", "3"]))
    monkeypatch.setattr(datapurge.config_store, "mutate", _mutate)
    ext = object.__new__(datapurge.DataPurgeExtension)
    ext.bot = SimpleNamespace(user=SimpleNamespace(_guild_ids={1}))
    ext.repo = Repo()
    ext.purge_left_guilds = Idle()
    return ext


def _stored(ids):
    async def guild_ids_with_data():
        return list(ids)

    return guild_ids_with_data


def _mutate(mutator):
    from src.core.config import config_store

    mutator(config_store._data)
    return config_store._data


async def test_guilds_left_while_offline_are_scheduled(ext):
    ext.repo.pending["1"] = datetime(2026, 1, 1, tzinfo=UTC)  # left once, then back
    await datapurge.DataPurgeExtension.on_startup.callback(ext)
    assert set(ext.repo.pending) == {"2", "3"}


async def test_due_guilds_lose_their_data_and_settings(ext, monkeypatch):
    from src.core.config import config_store

    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    ext.repo.pending = {"1": long_ago, "2": long_ago}
    await datapurge.DataPurgeExtension.purge_left_guilds.callback(ext)

    assert ext.repo.purged == ["2"]  # guild 1 is back: kept
    assert "2" not in config_store._data["servers"] and "1" in config_store._data["servers"]
    assert ext.repo.pending == {}


async def test_leaving_and_rejoining(ext):
    await datapurge.DataPurgeExtension.on_guild_left.callback(ext, SimpleNamespace(guild_id=5))
    assert "5" in ext.repo.pending
    await datapurge.DataPurgeExtension.on_guild_join.callback(ext, SimpleNamespace(guild_id=5))
    assert "5" not in ext.repo.pending
