"""Moderation commands: the ban deletion window, the untimeout hierarchy, the DM correction."""

from datetime import datetime
from types import SimpleNamespace

import pytest

import extensions.moderation.commands as commands_module
from extensions.moderation.commands import CommandsMixin
from features.moderation import Infraction


def member(uid, position=0):
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        mention=f"<@{uid}>",
        roles=[SimpleNamespace(position=position)],
        sent=[],
    )


class FakeGuild:
    def __init__(self, fail=False):
        self.id = 1
        self.name = "Serveur"
        self.me = member(99, position=50)
        self._owner_id = 1000
        self.fail = fail
        self.bans = []
        self.kicks = []

    async def ban(self, target, **kwargs):
        if self.fail:
            raise RuntimeError("Missing Permissions")
        self.bans.append(kwargs)

    async def kick(self, target, **kwargs):
        if self.fail:
            raise RuntimeError("Missing Permissions")
        self.kicks.append(kwargs)


class FakeCtx:
    def __init__(self, guild, author):
        self.guild = guild
        self.guild_id = guild.id
        self.author = author
        self.sent = []

    async def defer(self, **kwargs):
        pass

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))


@pytest.fixture
def ext(monkeypatch):
    monkeypatch.setattr(commands_module, "get_guild_settings", lambda gid: {"dmOnAction": True})

    async def ok(ctx):
        return True

    monkeypatch.setattr(commands_module, "require_guild", ok)

    class Ext(CommandsMixin):
        async def dm_target(self, settings, target, text):
            target.sent.append(text)

        async def log_case(self, *args, **kwargs):
            pass

        async def _record(self, ctx, **kwargs):
            return Infraction(
                id=1,
                guild_id="1",
                user_id="2",
                moderator_id="3",
                type=kwargs["mtype"],
                created_at=datetime.now(),
            )

    return Ext()


async def test_ban_deletes_the_requested_days_of_messages(ext):
    guild = FakeGuild()
    target = member(2, position=1)
    ctx = FakeCtx(guild, member(3, position=10))
    await CommandsMixin.ban.callback(ext, ctx, target, None, 7)
    [ban] = guild.bans
    assert ban["delete_message_seconds"] == 7 * 86400
    assert "delete_message_days" not in ban


async def test_a_failed_ban_corrects_the_dm(ext):
    guild = FakeGuild(fail=True)
    target = member(2, position=1)
    await CommandsMixin.ban.callback(ext, FakeCtx(guild, member(3, position=10)), target, None, 0)
    assert guild.bans == []
    assert len(target.sent) == 2 and "pas été banni" in target.sent[1]


async def test_untimeout_respects_the_hierarchy(ext):
    guild = FakeGuild()
    lifted = []

    async def timeout(**kwargs):
        lifted.append(kwargs)

    senior = member(2, position=20)
    senior.timeout = timeout
    ctx = FakeCtx(guild, member(3, position=10))
    await CommandsMixin.untimeout.callback(ext, ctx, senior, None)
    assert lifted == []
    assert ctx.sent  # the error reply
