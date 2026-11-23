"""Daily vote reminders: a member with closed DMs doesn't hold back the others."""

import importlib
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest


class FakeUser:
    def __init__(self, uid, closed_dms=False):
        self.id = uid
        self.mention = f"<@{uid}>"
        self.display_name = f"user{uid}"
        self.closed_dms = closed_dms
        self.sent = []

    async def send(self, text):
        if self.closed_dms:
            raise RuntimeError("Cannot send messages to this user")
        self.sent.append(text)


class FakeRepo:
    async def get_votes_doc(self, track_id):
        return {"votes": {"3": "conserver"}}  # member 3 already voted


@pytest.fixture
def votes_module(store_config):
    store_config({"config": {}, "servers": {}})
    for name in [m for m in sys.modules if m.startswith("extensions.spotify")]:
        sys.modules.pop(name)
    return importlib.import_module("extensions.spotify.votes")


@pytest.fixture
def users(monkeypatch, votes_module):
    by_id = {1: FakeUser(1, closed_dms=True), 2: FakeUser(2), 3: FakeUser(3)}

    async def fetch_user_safe(bot, uid):
        return None, by_id.get(uid)

    monkeypatch.setattr(votes_module, "fetch_user_safe", fetch_user_safe)
    return by_id


def server_due(remind_time, user_ids):
    return SimpleNamespace(
        guild_id="1",
        channel_id="2",
        repo=FakeRepo(),
        vote_infos={"track_id": "t", "message_id": "m"},
        reminders={remind_time: set(user_ids)},
        daily_vote=True,
    )


def mixin(votes_module):
    saved = []

    class Ext(votes_module.VotesMixin):
        bot = None

        async def save_reminders(self, server):
            saved.append(dict(server.reminders))

        async def ensure_state(self, server):
            return None

    ext = Ext()
    ext.saved = saved
    return ext


async def test_closed_dms_dont_stop_the_others(users, votes_module):
    due = datetime.now(UTC) - timedelta(minutes=1)
    server = server_due(due, [1, 2, 3])
    ext = mixin(votes_module)
    await ext._reminder_check_for_server(server)
    assert users[2].sent and not users[3].sent  # 3 already voted
    # everyone moves to tomorrow, the member with closed DMs included
    assert server.reminders == {due + timedelta(days=1): {1, 2, 3}}
    assert ext.saved


async def test_reminders_are_saved_even_when_the_vote_lookup_fails(users, votes_module):
    due = datetime.now(UTC) - timedelta(minutes=1)
    server = server_due(due, [2])
    server.vote_infos = {}  # no poll today: the lookup raises KeyError
    ext = mixin(votes_module)
    await ext._reminder_check_for_server(server)
    assert server.reminders == {due + timedelta(days=1): {2}}
    assert ext.saved


async def test_without_a_daily_vote_no_reminder_goes_out_but_each_keeps_its_hour(
    users, votes_module
):
    """P28: ``voteEnabled`` off, there is nothing to remind of; switched back on, they resume."""
    due = datetime.now(UTC) - timedelta(minutes=1)
    server = server_due(due, [2])
    server.daily_vote = False
    ext = mixin(votes_module)
    await ext._reminder_check_for_server(server)
    assert users[2].sent == []
    assert server.reminders == {due + timedelta(days=1): {2}}


async def test_a_reminder_cannot_be_set_without_a_daily_vote(votes_module):
    from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, run

    guild = FakeGuild(1)
    ctx = FakeCtx(FakeMember(guild=guild), guild.add_channel(FakeChannel(2)))
    server = server_due(datetime.now(UTC), [])
    server.daily_vote = False
    ext = mixin(votes_module)
    ext.get_server = lambda guild_id: server
    await run(votes_module.VotesMixin.setreminder, ext, ctx, heure=20, minute=0)
    assert "pas de vote quotidien" in ctx.last and "tableau de bord" in ctx.last
    assert all(not ids for ids in server.reminders.values())  # nobody was added
    assert ext.saved == []
