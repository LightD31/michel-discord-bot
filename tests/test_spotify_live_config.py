"""Spotify's per-guild state reads its settings live: a save applies without a reload."""

import importlib
import sys

import pytest

from src.core.config import config_store

GUILD = "42"


def _config(tmp_path, **spotify) -> dict:
    return {
        "config": {
            "misc": {"dataFolder": str(tmp_path)},
            "webui": {"enabled": True, "baseUrl": "https://bot.ex"},
        },
        "servers": {
            GUILD: {"moduleSpotify": {"enabled": True, **spotify}, "discord2name": {"7": "Alice"}}
        },
    }


@pytest.fixture
def spotify(store_config, tmp_path, monkeypatch):
    store_config(_config(tmp_path, spotifyChannelId="100"))
    sys.modules.pop("extensions.spotify._common", None)
    module = importlib.import_module("extensions.spotify._common")
    yield module
    sys.modules.pop("extensions.spotify._common", None)


def test_the_guilds_enabled_at_load_get_their_state(spotify):
    assert list(spotify.SERVERS) == [GUILD]


def test_settings_follow_a_save(spotify, tmp_path):
    server = spotify.SERVERS[GUILD]
    assert server.channel_id == "100"
    assert server.discord2name == {"7": "Alice"}

    config_store._set_cache(
        _config(
            tmp_path,
            spotifyChannelId="200",
            spotifyUsers=[{"spotifyId": "sp1", "discordId": "7"}],
            voteReminderEveryDays="3",
        )
    )

    assert server.channel_id == "200"
    assert server.spotify2discord == {"sp1": "7"}
    assert server.vote_reminder_every_days == 3
    assert server.vote_manager.discord2name == {"7": "Alice"}


def test_turning_the_public_page_on_mints_its_token_on_first_use(spotify, tmp_path, monkeypatch):
    server = spotify.SERVERS[GUILD]
    assert server.links.dashboard in ("", None)

    minted: list[str] = []

    def fake_ensure(guild_id, **_):
        minted.append(guild_id)
        return "tok"

    monkeypatch.setattr(spotify, "ensure_public_token", fake_ensure)
    config_store._set_cache(_config(tmp_path, publicDashboard=True))

    assert "tok" in (server.links.dashboard or "")
    assert minted == [GUILD]
