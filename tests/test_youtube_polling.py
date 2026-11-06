"""YouTube polling: one guild or channel failing must not stop the rest, and the key stays out of URLs."""

import asyncio
import importlib
import sys

import pytest

from src.core.errors import HttpError

GUILD_CONFIG = {
    "A": {"ChannelId": "100", "youtubeChannelList": {"@alpha": {"vod": True}}},
    "B": {
        "ChannelId": "200",
        "youtubeChannelList": {"@broken": {"vod": True}, "@bravo": {"vod": True}},
    },
}


@pytest.fixture
def youtube(monkeypatch):
    import src.core.config as core_config

    def fake_load_config(module_name=None):
        return {"youtube": {"youtubeApiKey": "SECRET-KEY"}}, GUILD_CONFIG, list(GUILD_CONFIG)

    monkeypatch.setattr(core_config, "load_config", fake_load_config)
    sys.modules.pop("extensions.youtube", None)
    module = importlib.import_module("extensions.youtube")
    yield module
    sys.modules.pop("extensions.youtube", None)


class Channel:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, content):
        self.sent.append(content)


class Bot:
    def __init__(self, channels, failing=()):
        self.channels = channels
        self.failing = set(failing)

    async def fetch_channel(self, channel_id):
        if channel_id in self.failing:
            raise RuntimeError("Unknown Channel")
        return self.channels[channel_id]


class Repo:
    stored: dict = {}
    loads = 0

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def get_last_video_ids(self):
        type(self).loads += 1
        return self.stored.get(self.guild_id)

    async def save_last_video_ids(self, ids):
        self.stored[self.guild_id] = dict(ids)


def install_fakes(monkeypatch, module, *, latest, calls):
    """Fake the API: *latest* maps handle → newest video id ("boom" raises)."""

    async def fake_fetch(url, return_type="text", headers=None, params=None, **_):
        calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "channels":
            handle = params["forHandle"]
            if latest.get(handle) == "boom":
                raise HttpError("Failed to fetch", url=url, status=404)
            return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": handle}}}]}
        if endpoint == "playlistItems":
            video = latest[params["playlistId"]]
            return {"items": [{"snippet": {"resourceId": {"videoId": video}}}]}
        return {
            "items": [
                {
                    "snippet": {"liveBroadcastContent": "none"},
                    "contentDetails": {"duration": "PT10M"},
                }
            ]
        }

    async def identity(text, **_):
        return text

    Repo.stored = {}
    Repo.loads = 0
    monkeypatch.setattr(module, "fetch", fake_fetch)
    monkeypatch.setattr(module, "YoutubeRepository", Repo)
    monkeypatch.setattr(module, "shorten_text", identity)


def make_extension(module, bot):
    ext = object.__new__(module.YoutubeExtension)  # skip interactions' Extension wiring
    ext.bot = bot
    ext.playlist_cache = {}
    return ext


def tick(module, ext):
    asyncio.run(module.YoutubeExtension.check_youtube.callback(ext))


def test_failures_stay_local_and_progress_is_saved(youtube, monkeypatch):
    calls: list = []
    latest = {"alpha": "a1", "broken": "boom", "bravo": "b1"}
    install_fakes(monkeypatch, youtube, latest=latest, calls=calls)
    channels = {"100": Channel(), "200": Channel()}
    # Both guilds already synced once, on older videos.
    Repo.stored = {"A": {"@alpha": "a0"}, "B": {"@broken": "x0", "@bravo": "b0"}}
    ext = make_extension(youtube, Bot(channels, failing={"100"}))  # guild A's channel is gone

    tick(youtube, ext)

    assert Repo.loads == len(GUILD_CONFIG)  # state read once per tick, not once per guild
    # Guild A failed as a whole; guild B still posted @bravo and saved it.
    assert channels["200"].sent == ["https://www.youtube.com/watch?v=b1"]
    assert Repo.stored["B"] == {"@broken": "x0", "@bravo": "b1"}  # @broken retried next tick
    assert Repo.stored["A"] == {"@alpha": "a0"}

    # Next tick, everything recovered: A posts, B's broken handle comes back.
    ext.bot.failing.clear()
    latest["broken"] = "x1"
    tick(youtube, ext)
    assert channels["100"].sent == ["https://www.youtube.com/watch?v=a1"]
    assert channels["200"].sent[-1] == "https://www.youtube.com/watch?v=x1"
    assert Repo.stored == {"A": {"@alpha": "a1"}, "B": {"@broken": "x1", "@bravo": "b1"}}


def test_initial_sync_records_without_posting(youtube, monkeypatch):
    calls: list = []
    install_fakes(
        monkeypatch, youtube, latest={"alpha": "a1", "broken": "x1", "bravo": "b1"}, calls=calls
    )
    channels = {"100": Channel(), "200": Channel()}
    ext = make_extension(youtube, Bot(channels))

    tick(youtube, ext)

    assert channels["100"].sent == [] and channels["200"].sent == []
    assert Repo.stored == {"A": {"@alpha": "a1"}, "B": {"@broken": "x1", "@bravo": "b1"}}


def test_api_key_never_appears_in_a_url(youtube, monkeypatch):
    calls: list = []
    install_fakes(
        monkeypatch, youtube, latest={"alpha": "a1", "broken": "x1", "bravo": "b1"}, calls=calls
    )
    Repo.stored = {"A": {"@alpha": "a0"}}
    ext = make_extension(youtube, Bot({"100": Channel(), "200": Channel()}))

    tick(youtube, ext)

    assert calls
    for call in calls:
        assert "SECRET-KEY" not in call["url"]
        assert "key" not in call["params"]
        assert call["headers"]["X-Goog-Api-Key"] == "SECRET-KEY"
