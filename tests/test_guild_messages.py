"""Persistent bot messages: found again across restarts, posted once, tracked in MongoDB.

Where each message lives is a ``bot_messages`` document per guild and slot
(src.core.message_refs), never a config field; the repository is faked here,
its MongoDB behaviour is covered by tests/test_mongo_message_refs.py.
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.discord_ext.messages as messages
from src.core.errors import DatabaseError
from src.core.message_refs import MessageRef
from src.discord_ext.messages import (
    GuildMessages,
    fetch_or_create_persistent_message,
    forget_persistent_message,
    republish_persistent_message,
)


class FakeRepo:
    """The ``bot_messages`` collection of every guild, as a dict."""

    docs: dict[tuple[str, str], MessageRef] = {}
    failing = False

    def __init__(self, guild_id):
        self.guild_id = str(guild_id)

    async def get(self, slot):
        if FakeRepo.failing:
            raise DatabaseError("mongo down")
        return FakeRepo.docs.get((self.guild_id, slot))

    async def save(self, slot, channel_id, message_id, label=""):
        FakeRepo.docs[(self.guild_id, slot)] = MessageRef(slot, channel_id, message_id, label)

    async def delete(self, slot):
        return FakeRepo.docs.pop((self.guild_id, slot), None)


class FakeChannel:
    def __init__(self, cid: str, bot: "FakeBot") -> None:
        self.id = cid
        self.bot = bot

    async def send(self, content):
        self.bot.next_id += 1
        message = SimpleNamespace(
            id=self.bot.next_id, channel=self, content=content, pinned=False, deleted=False
        )

        async def pin():
            message.pinned = True

        async def delete():
            message.deleted = True
            self.bot.messages.pop(message.id, None)

        message.pin, message.delete = pin, delete
        self.bot.messages[message.id] = message
        self.bot.sent.append(message)
        return message

    async def fetch_message(self, message_id):
        self.bot.fetched.append(message_id)
        message = self.bot.messages.get(int(message_id))
        if message is None or message.channel is not self:
            raise LookupError("Unknown Message")
        return message


class FakeBot:
    def __init__(self) -> None:
        self.next_id = 100
        self.messages: dict[int, SimpleNamespace] = {}
        self.channels: dict[str, FakeChannel] = {}
        self.sent: list[SimpleNamespace] = []
        self.fetched: list = []
        self.channel_fetches = 0

    async def fetch_channel(self, cid):
        self.channel_fetches += 1
        return self.channels.setdefault(str(cid), FakeChannel(str(cid), self))


@pytest.fixture(autouse=True)
def store(monkeypatch):
    FakeRepo.docs = {}
    FakeRepo.failing = False
    monkeypatch.setattr(messages, "MessageRefRepository", FakeRepo)
    monkeypatch.setattr(messages, "_resolved", {})
    monkeypatch.setattr(messages, "_slot_locks", {})
    return FakeRepo


def restart():
    """A new process: nothing resolved in memory, MongoDB still there."""
    messages._resolved.clear()


async def get(bot, channel="10", slot="status", pin=False, guild="1"):
    return await fetch_or_create_persistent_message(
        bot, guild_id=guild, slot=slot, channel_id=channel, pin=pin, label="Statut"
    )


async def test_a_message_is_posted_once_then_reused(store):
    bot = FakeBot()

    first = await get(bot, pin=True)
    again = await get(bot)

    assert again is first
    assert len(bot.sent) == 1 and first.pinned
    assert bot.channel_fetches == 1  # the second call never left the process
    assert store.docs[("1", "status")] == MessageRef("status", "10", str(first.id), "Statut")


async def test_a_restart_finds_the_stored_message_instead_of_posting(store):
    bot = FakeBot()
    first = await get(bot)
    restart()

    again = await get(bot)

    assert again is first
    assert len(bot.sent) == 1
    assert bot.fetched == [first.id]


async def test_a_new_channel_gets_a_new_message(store):
    bot = FakeBot()
    first = await get(bot, channel="10")

    moved = await get(bot, channel="20")

    assert moved is not first and moved.channel.id == "20"
    assert store.docs[("1", "status")].channel_id == "20"


async def test_a_message_deleted_in_discord_is_posted_again(store):
    bot = FakeBot()
    first = await get(bot)
    await first.delete()
    restart()

    again = await get(bot)

    assert again is not first
    assert store.docs[("1", "status")].message_id == str(again.id)


async def test_an_unreadable_store_posts_nothing(store):
    """Posting blind would leave a duplicate behind at every restart."""
    store.failing = True
    bot = FakeBot()

    assert await get(bot) is None
    assert bot.sent == []

    store.failing = False
    assert await get(bot) is not None  # retried at the next refresh


async def test_no_channel_means_no_message(store):
    bot = FakeBot()
    assert await get(bot, channel=None) is None
    assert bot.channel_fetches == 0


async def test_racing_refreshes_post_a_single_message(store):
    bot = FakeBot()

    first, second = await asyncio.gather(get(bot), get(bot))

    assert first is second
    assert len(bot.sent) == 1


async def test_slots_and_guilds_are_independent(store):
    bot = FakeBot()
    status = await get(bot, slot="status")
    live = await get(bot, slot="live")
    other = await get(bot, guild="2")

    assert len({status.id, live.id, other.id}) == 3
    assert set(store.docs) == {("1", "status"), ("1", "live"), ("2", "status")}


async def test_forgetting_resolves_again_without_posting(store):
    bot = FakeBot()
    first = await get(bot)

    forget_persistent_message("1", "status")
    again = await get(bot)

    assert again is first and len(bot.sent) == 1
    assert bot.fetched == [first.id]


async def test_republishing_deletes_the_message_and_posts_it_anew(store):
    bot = FakeBot()
    first = await get(bot)

    assert await republish_persistent_message(bot, "1", "status") is True
    assert first.deleted and ("1", "status") not in store.docs

    again = await get(bot)
    assert again is not first and len(bot.sent) == 2


async def test_republishing_an_unknown_slot_says_so(store):
    assert await republish_persistent_message(FakeBot(), "1", "nope") is False


# ── GuildMessages ────────────────────────────────────────────────────────


def _guild_messages(bot) -> GuildMessages:
    return GuildMessages(bot, slot="status", channel_key="channelId", pin_key="pin", label="Statut")


async def test_guild_messages_read_the_channel_and_pin_from_the_settings(store):
    bot = FakeBot()
    msgs = _guild_messages(bot)

    message = await msgs.get(1, {"channelId": "10", "pin": True})

    assert message.channel.id == "10" and message.pinned
    assert msgs.cached("1") is message
    assert store.docs[("1", "status")].label == "Statut"


async def test_guild_messages_without_a_channel_give_none(store):
    msgs = _guild_messages(FakeBot())
    await msgs.get("1", {"channelId": "10"})

    assert await msgs.get("1", {}) is None
    assert msgs.cached("1") is None


async def test_guild_messages_drop_the_guilds_switched_off(store):
    bot = FakeBot()
    msgs = _guild_messages(bot)
    await msgs.get("1", {"channelId": "10"})
    await msgs.get("2", {"channelId": "20"})

    msgs.keep_only(["2"])

    assert msgs.cached("1") is None and msgs.cached("2") is not None
    # Its message is still where it was: found again, not posted twice.
    await msgs.get("1", {"channelId": "10"})
    assert len(bot.sent) == 2
