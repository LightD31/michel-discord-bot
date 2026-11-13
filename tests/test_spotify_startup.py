"""Spotify's tasks start even when a guild's state can't be read at startup (B05)."""

import importlib
import sys

import pytest

from src.core.errors import DatabaseError

TASKS = (
    "check_playlist_changes",
    "randomvote",
    "reminder_check",
    "check_for_end",
    "new_titles_playlist",
)


class FakeTask:
    def __init__(self):
        self.running = False
        self.starts = 0

    def start(self):
        self.running = True
        self.starts += 1


class Repo:
    def __init__(self):
        self.down = True

    async def get_vote_infos(self):
        if self.down:
            raise DatabaseError("mongo down")
        return {"_id": "x", "message_id": "5", "track_id": "t"}

    async def get_snapshot(self):
        return None

    async def list_reminders(self):
        return [{"_id": "2026-01-01 20:00:00", "user_ids": [1]}]

    async def reattribute_added_by(self, mapping):
        return 0

    async def added_by_counts(self):
        return {}


@pytest.fixture
def spotify(store_config, tmp_path):
    store_config(
        {
            "config": {"misc": {"dataFolder": str(tmp_path)}},
            "servers": {"1": {"moduleSpotify": {"enabled": True}}},
        }
    )
    for name in [m for m in sys.modules if m.startswith("extensions.spotify")]:
        del sys.modules[name]
    module = importlib.import_module("extensions.spotify")
    yield module
    for name in [m for m in sys.modules if m.startswith("extensions.spotify")]:
        del sys.modules[name]


def make_extension(module):
    ext = object.__new__(module.SpotifyExtension)
    for name in TASKS:
        setattr(ext, name, FakeTask())
    server = module.SERVERS["1"]
    server.repo = Repo()
    return ext, server


async def test_tasks_start_when_the_state_cant_be_read(spotify):
    ext, server = make_extension(spotify)

    await spotify.SpotifyExtension.on_startup.callback(ext)

    assert all(getattr(ext, name).running for name in TASKS)
    assert server.loaded is False


async def test_the_state_is_loaded_on_first_use_once_mongodb_is_back(spotify):
    ext, server = make_extension(spotify)
    await spotify.SpotifyExtension.on_startup.callback(ext)

    with pytest.raises(DatabaseError):
        await ext.ensure_state(server)
    server.repo.down = False
    await ext.ensure_state(server)

    assert server.loaded is True
    assert server.vote_infos == {"message_id": "5", "track_id": "t"}
    assert len(server.reminders) == 1


async def test_a_second_startup_doesnt_restart_running_tasks(spotify):
    ext, server = make_extension(spotify)
    server.repo.down = False
    await spotify.SpotifyExtension.on_startup.callback(ext)
    await spotify.SpotifyExtension.on_startup.callback(ext)
    assert all(getattr(ext, name).starts == 1 for name in TASKS)
