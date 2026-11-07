"""Extensions that record a message id write through the config store.

They used to rewrite ``config.json`` by hand: outside the write lock (a
dashboard save at the same moment could be lost), and behind the store's back,
so the live reads (``ModuleConfig``) kept the old id and posted the message again.
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
                        "moduleVlrgg": {"enabled": True, "teams": [{"name": "KC", "id": "1"}]},
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


def test_vlrgg_records_a_team_message_in_the_file_and_the_store(config_file):
    from extensions.vlrgg._common import _save_team_channel_message

    _save_team_channel_message(GUILD, "KC", "100", "200")
    _save_team_channel_message(GUILD, "NEW", "100", "300")

    expected = [
        {"name": "KC", "id": "1", "channelMessageId": "100:200"},
        {"name": "NEW", "channelMessageId": "100:300"},
    ]
    assert _on_disk(config_file)["moduleVlrgg"]["teams"] == expected
    assert ModuleConfig("moduleVlrgg").get(GUILD)["teams"] == expected


def test_twitch_records_a_planning_message_in_the_file_and_the_store(config_file):
    from extensions.twitch import _save_streamer_channel_message

    _save_streamer_channel_message(GUILD, "streamer", "100", "200", True)

    expected = {
        "twitchPlanningChannelId": "100",
        "twitchPlanningMessageId": "200",
        "twitchPlanningPinMessage": True,
    }
    assert _on_disk(config_file)["moduleTwitch"]["twitchStreamerList"]["streamer"] == expected
    streamers = ModuleConfig("moduleTwitch").get(GUILD)["twitchStreamerList"]
    assert streamers["streamer"] == expected


@pytest.mark.parametrize(
    ("channel_id", "message_id", "stored"),
    [("100", "200", "100:200"), ("100", None, "100"), (None, None, "")],
)
def test_mdi_records_its_schedule_message(config_file, channel_id, message_id, stored):
    from extensions.mdi._common import save_schedule_channel_message_id

    save_schedule_channel_message_id(GUILD, channel_id, message_id)

    assert _on_disk(config_file)["moduleMDI"]["scheduleChannelMessageId"] == stored
    assert ModuleConfig("moduleMDI").get(GUILD)["scheduleChannelMessageId"] == stored


def test_a_write_keeps_a_concurrent_dashboard_edit(config_file):
    # The old hand-written writers re-read the file outside the lock; going
    # through mutate, an edit made just before is kept.
    from extensions.vlrgg._common import _save_team_channel_message

    config_store.mutate(lambda data: data["servers"][GUILD]["moduleMDI"].update(teamSlug="x"))
    _save_team_channel_message(GUILD, "KC", "100", "200")

    assert _on_disk(config_file)["moduleMDI"]["teamSlug"] == "x"
