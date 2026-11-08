"""The dashboard's channel pickers: what src/webui/channels.py lists for a guild."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from interactions import ChannelType

from src.webui.channels import list_channels


def chan(cid, ctype, name, position=0, **extra):
    return SimpleNamespace(id=cid, type=ctype, name=name, position=position, **extra)


class Guild:
    def __init__(self, channels, threads=(), *, fetch_fails=False, threads_fail=False):
        self.channels = list(channels)
        self.threads = list(threads)
        self.fetch_fails = fetch_fails
        self.threads_fail = threads_fail

    async def fetch_channels(self):
        if self.fetch_fails:
            raise RuntimeError("HTTP 500")
        return self.channels

    async def fetch_active_threads(self):
        if self.threads_fail:
            raise RuntimeError("HTTP 403")
        return SimpleNamespace(threads=self.threads)


class Bot:
    def __init__(self, guild) -> None:
        self.guild = guild

    async def fetch_guild(self, guild_id):
        if isinstance(self.guild, Exception):
            raise self.guild
        return self.guild


CHANNELS = [
    chan(1, ChannelType.GUILD_CATEGORY, "Général", 1),
    chan(2, ChannelType.GUILD_TEXT, "annonces", 2, parent_id=1),
    chan(3, ChannelType.GUILD_NEWS, "news", 1, parent_id=1),
    chan(4, ChannelType.GUILD_VOICE, "vocal", 0),
    chan(5, ChannelType.GUILD_PUBLIC_THREAD, "fil", 3, parent_id=2, archived=True),
    chan(9, ChannelType.GUILD_CATEGORY, "Archives", 0),
]


async def test_text_channels_threads_and_categories_are_listed_in_order():
    data = await list_channels(Bot(Guild(CHANNELS)), "1")

    assert [c["id"] for c in data["channels"]] == ["3", "2", "5"]  # by position, voice left out
    assert data["channels"][2] == {
        "id": "5",
        "name": "fil",
        "parent_id": "2",
        "position": 3,
        "is_thread": True,
        "archived": True,
    }
    assert [c["id"] for c in data["categories"]] == ["9", "1"]


async def test_active_threads_are_added_once():
    private = chan(7, ChannelType.GUILD_PRIVATE_THREAD, "privé", parent_id=2)
    data = await list_channels(Bot(Guild(CHANNELS, threads=[private, CHANNELS[4]])), "1")

    ids = [c["id"] for c in data["channels"]]
    assert ids.count("5") == 1 and "7" in ids


async def test_the_gateway_cache_stands_in_when_discord_fails():
    data = await list_channels(Bot(Guild(CHANNELS, fetch_fails=True, threads_fail=True)), "1")
    assert [c["id"] for c in data["channels"]] == ["3", "2", "5"]


async def test_an_unknown_guild_is_a_404_without_details():
    with pytest.raises(HTTPException) as caught:
        await list_channels(Bot(LookupError("Unknown Guild 10004")), "1")
    assert caught.value.status_code == 404
    assert "10004" not in caught.value.detail
