"""GuildMessages: one persistent message per guild, refetched when its channel changes."""

from types import SimpleNamespace

import pytest

import src.discord_ext.messages as messages
from src.discord_ext.messages import GuildMessages


@pytest.fixture
def fetches(monkeypatch):
    calls: list[dict] = []

    async def fake_fetch(bot, **kwargs):
        calls.append(kwargs)
        if kwargs["channel_id"] == "404":
            return None
        return SimpleNamespace(id=len(calls), channel=kwargs["channel_id"])

    monkeypatch.setattr(messages, "fetch_or_create_persistent_message", fake_fetch)
    return calls


def _messages() -> GuildMessages:
    return GuildMessages(
        object(),  # type: ignore[arg-type]
        module_name="moduleX",
        channel_key="channelId",
        message_key="messageId",
        pin_key="pin",
        initial_content="…",
    )


async def test_the_message_is_fetched_once_per_channel(fetches):
    msgs = _messages()
    settings = {"channelId": "10", "messageId": "99", "pin": True}

    first = await msgs.get("1", settings)
    again = await msgs.get(1, settings)

    assert first is again
    assert msgs.cached(1) is first
    assert len(fetches) == 1
    assert fetches[0] == {
        "channel_id": "10",
        "message_id": "99",
        "module_name": "moduleX",
        "message_id_key": "messageId",
        "guild_id": "1",
        "initial_content": "…",
        "pin": True,
        "logger": None,
    }


async def test_a_new_channel_fetches_again(fetches):
    msgs = _messages()
    await msgs.get("1", {"channelId": "10"})
    moved = await msgs.get("1", {"channelId": "20"})

    assert moved.channel == "20"
    assert [c["channel_id"] for c in fetches] == ["10", "20"]


async def test_guilds_are_independent_and_can_be_dropped(fetches):
    msgs = _messages()
    await msgs.get("1", {"channelId": "10"})
    await msgs.get("2", {"channelId": "20"})

    msgs.keep_only(["2"])
    assert msgs.cached("1") is None and msgs.cached("2") is not None

    msgs.forget("2")
    await msgs.get("2", {"channelId": "20"})
    assert len(fetches) == 3


async def test_no_channel_or_a_failed_fetch_gives_none(fetches):
    msgs = _messages()
    assert await msgs.get("1", {}) is None
    assert fetches == []

    assert await msgs.get("1", {"channelId": "404"}) is None
    assert msgs.cached("1") is None
    await msgs.get("1", {"channelId": "404"})
    assert len(fetches) == 2  # a failure is retried at the next refresh
