"""Reaction polls with a deadline are persisted, so their auto-close survives a restart."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from discord_fakes import discord_error, not_found
from interactions import Embed
from interactions.client.errors import HTTPException

import extensions.polls as polls_module
from extensions.polls import PollsExtension
from features.polls import POLL_EMOJIS, Poll, options_description
from src.discord_ext.embeds import Colors


def test_poll_model_accepts_reaction_polls():
    poll = Poll(
        channel_id="1", message_id="2", author_id="3", question="q", options=["a"], mode="reaction"
    )
    assert poll.emojis == []


def test_options_description():
    assert options_description(["Oui", "Non"], ["👍", "👎"]) == "👍 Oui\n\n👎 Non"


class FakeRepo:
    def __init__(self, fail=False):
        self.fail = fail
        self.added: list[Poll] = []
        self.closed: list[str] = []
        self.due: list[Poll] = []

    async def add(self, poll):
        if self.fail:
            raise RuntimeError("mongo down")
        self.added.append(poll)
        return "a" * 24

    async def list_due(self, now):
        return list(self.due)

    async def mark_closed(self, poll_id):
        self.closed.append(poll_id)


class FakeMessage:
    def __init__(self, message_id=10):
        self.id = message_id
        self.channel = SimpleNamespace(id=20)
        self.embeds = [
            Embed(title="Pizza ?", description="1️⃣ Oui\n\n*Fermeture…*", color=Colors.UTIL)
        ]
        self.reactions_cleared = False

    async def clear_all_reactions(self):
        self.reactions_cleared = True


def make_extension(repo, bot=None):
    ext = object.__new__(PollsExtension)  # skip interactions' Extension wiring
    ext.bot = bot
    ext._poll_repo = lambda guild_id: repo
    ext._close_failures = {}
    return ext


def ctx(guild_id):
    return SimpleNamespace(guild_id=guild_id, user=SimpleNamespace(id=7))


@pytest.fixture
def enabled(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleUtils": {"enabled": True}}}})
    edits: list = []

    async def fake_edit(message, **kwargs):
        edits.append((message, kwargs))

    monkeypatch.setattr(polls_module, "edit_message_if_changed", fake_edit)
    return edits


def schedule(ext, guild_id, seconds=3600):
    async def main():
        await ext._record_reaction_poll(
            ctx(guild_id), FakeMessage(), seconds, "Pizza ?", ["Oui", "Non"], POLL_EMOJIS
        )
        tasks = dict(polls_module._reaction_close_tasks)
        for task in tasks.values():
            task.cancel()
        polls_module._reaction_close_tasks.clear()
        return tasks

    return asyncio.run(main())


def test_poll_in_an_enabled_server_is_persisted(enabled):
    repo = FakeRepo()
    tasks = schedule(make_extension(repo), guild_id=1)
    assert tasks == {}  # nothing left in memory
    (stored,) = repo.added
    assert stored.mode == "reaction" and stored.options == ["Oui", "Non"]
    assert stored.emojis == POLL_EMOJIS[:2]
    assert stored.closes_at > datetime.now(UTC) + timedelta(minutes=59)


def test_a_poll_without_a_deadline_is_stored_too(enabled):
    # The reaction listener and /editpoll only act on stored polls.
    repo = FakeRepo()
    assert schedule(make_extension(repo), guild_id=1, seconds=None) == {}
    (stored,) = repo.added
    assert stored.mode == "reaction" and stored.closes_at is None


def test_a_poll_without_a_deadline_elsewhere_needs_no_timer(enabled):
    repo = FakeRepo()
    assert schedule(make_extension(repo), guild_id=999, seconds=None) == {}
    assert repo.added == []


def test_falls_back_to_memory_outside_enabled_servers_or_on_db_error(enabled):
    repo = FakeRepo()
    assert len(schedule(make_extension(repo), guild_id=999)) == 1  # user install elsewhere
    assert repo.added == []
    assert len(schedule(make_extension(FakeRepo(fail=True)), guild_id=1)) == 1


def test_the_sweep_closes_a_due_reaction_poll(enabled):
    message = FakeMessage()

    async def fetch_message(message_id):
        return message

    channel = SimpleNamespace(fetch_message=fetch_message)

    async def fetch_channel(channel_id):
        return channel

    repo = FakeRepo()
    repo.due = [
        Poll(
            _id="b" * 24,
            channel_id="20",
            message_id="10",
            author_id="7",
            question="Pizza ?",
            options=["Oui", "Non"],
            emojis=["1️⃣", "2️⃣"],
            mode="reaction",
            closes_at=datetime.now() - timedelta(minutes=1),
        )
    ]
    ext = make_extension(repo, bot=SimpleNamespace(fetch_channel=fetch_channel))
    asyncio.run(PollsExtension.check_closing_polls.callback(ext))

    (edited, kwargs), *_ = enabled
    embed = kwargs["embed"]
    assert embed.title == "🔒 Pizza ?"
    assert embed.description == "1️⃣ Oui\n\n2️⃣ Non"  # deadline note gone
    assert embed.color == Colors.WARNING
    assert message.reactions_cleared
    assert repo.closed == ["b" * 24]


def due_poll(mode="reaction"):
    return Poll(
        _id="c" * 24,
        channel_id="20",
        message_id="10",
        author_id="7",
        question="q",
        options=["a"],
        mode=mode,
    )


def test_a_vanished_poll_is_marked_closed(enabled):
    async def fetch_channel(channel_id):
        return None  # interactions.py answers a deleted channel with None

    repo = FakeRepo()
    repo.due = [due_poll()]
    ext = make_extension(repo, bot=SimpleNamespace(fetch_channel=fetch_channel))
    asyncio.run(PollsExtension.check_closing_polls.callback(ext))
    assert repo.closed == ["c" * 24] and enabled == []


@pytest.mark.parametrize("mode", ["reaction", "anonymous"])
def test_a_deleted_message_closes_the_poll(enabled, mode):
    async def fetch_message(message_id):
        raise not_found()

    async def fetch_channel(channel_id):
        return SimpleNamespace(fetch_message=fetch_message)

    repo = FakeRepo()
    repo.due = [due_poll(mode)]
    ext = make_extension(repo, bot=SimpleNamespace(fetch_channel=fetch_channel))
    asyncio.run(PollsExtension.check_closing_polls.callback(ext))
    assert repo.closed == ["c" * 24]


@pytest.mark.parametrize("mode", ["reaction", "anonymous"])
def test_discord_not_answering_keeps_the_poll_open_for_a_while(enabled, mode, monkeypatch):
    monkeypatch.setattr(polls_module, "MAX_CLOSE_FAILURES", 3)

    async def fetch_channel(channel_id):
        raise discord_error(HTTPException, 503)

    repo = FakeRepo()
    repo.due = [due_poll(mode)]
    ext = make_extension(repo, bot=SimpleNamespace(fetch_channel=fetch_channel))
    for _ in range(2):
        asyncio.run(PollsExtension.check_closing_polls.callback(ext))
    assert repo.closed == []  # still due: the next sweep tries again

    asyncio.run(PollsExtension.check_closing_polls.callback(ext))
    assert repo.closed == ["c" * 24]  # gave up after MAX_CLOSE_FAILURES
    assert ext._close_failures == {}


def test_an_unload_cancels_the_in_memory_timers(enabled):
    async def main():
        ext = make_extension(FakeRepo())
        await ext._record_reaction_poll(
            ctx(999), FakeMessage(), 3600, "Pizza ?", ["Oui", "Non"], POLL_EMOJIS
        )
        (task,) = polls_module._reaction_close_tasks.values()
        await ext.async_drop()
        await asyncio.sleep(0)
        return task

    task = asyncio.run(main())
    assert task.cancelled()
    assert polls_module._reaction_close_tasks == {}
