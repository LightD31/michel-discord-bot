"""A poll due to close: gone, deleted, or Discord not answering for a while."""

import asyncio
from types import SimpleNamespace

import pytest
from discord_fakes import discord_error, not_found
from interactions.client.errors import HTTPException

import extensions.polls as polls_module
from extensions.polls import PollsExtension
from features.polls import Poll


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


def make_extension(repo, bot=None):
    ext = object.__new__(PollsExtension)  # skip interactions' Extension wiring
    ext.bot = bot
    ext._poll_repo = lambda guild_id: repo
    ext._close_failures = {}
    return ext


@pytest.fixture
def enabled(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleUtils": {"enabled": True}}}})
    edits: list = []

    async def fake_edit(message, **kwargs):
        edits.append((message, kwargs))

    monkeypatch.setattr(polls_module, "edit_message_if_changed", fake_edit)
    return edits


def due_poll(mode="anonymous"):
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


@pytest.mark.parametrize("mode", ["anonymous", "ranked"])
def test_a_deleted_message_closes_the_poll(enabled, mode):
    async def fetch_message(message_id):
        raise not_found()

    async def fetch_channel(channel_id):
        return SimpleNamespace(fetch_message=fetch_message, _guild_id=1)

    repo = FakeRepo()
    repo.due = [due_poll(mode)]
    ext = make_extension(repo, bot=SimpleNamespace(fetch_channel=fetch_channel))
    asyncio.run(PollsExtension.check_closing_polls.callback(ext))
    assert repo.closed == ["c" * 24]


@pytest.mark.parametrize("mode", ["anonymous", "ranked"])
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
