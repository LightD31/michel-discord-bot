"""A command that changes the config writes through the config store.

Writers used to rewrite ``config.json`` by hand: outside the write lock (a
dashboard save at the same moment could be lost), and behind the store's back,
so the live reads (``ModuleConfig``) kept the old value. Message ids are no
longer config at all (``bot_messages``); ``/twitch-planning-setup`` still records
where a streamer's planning goes.
"""

import json

import pytest

from src.core import config as core_config
from src.core.config import ModuleConfig, config_store

GUILD = "42"


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "config": {},
                "servers": {
                    GUILD: {
                        "moduleTwitch": {"enabled": True, "twitchStreamerList": {}},
                        "moduleMDI": {"enabled": True},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(path))
    config_store._data = None
    yield path
    config_store._data = None


def _on_disk(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["servers"][GUILD]


def test_twitch_records_a_planning_channel_in_the_file_and_the_store(config_file):
    from extensions.twitch import _save_streamer_planning

    _save_streamer_planning(GUILD, "streamer", "100", True)

    # The message itself is tracked in ``bot_messages``, never in the config.
    expected = {"twitchPlanningChannelId": "100", "twitchPlanningPinMessage": True}
    assert _on_disk(config_file)["moduleTwitch"]["twitchStreamerList"]["streamer"] == expected
    streamers = ModuleConfig("moduleTwitch").get(GUILD)["twitchStreamerList"]
    assert streamers["streamer"] == expected


def test_a_write_keeps_a_concurrent_dashboard_edit(config_file):
    # The old hand-written writers re-read the file outside the lock; going
    # through mutate, an edit made just before is kept.
    from extensions.twitch import _save_streamer_planning

    config_store.mutate(lambda data: data["servers"][GUILD]["moduleMDI"].update(teamSlug="x"))
    _save_streamer_planning(GUILD, "streamer", "100", False)

    assert _on_disk(config_file)["moduleMDI"]["teamSlug"] == "x"
