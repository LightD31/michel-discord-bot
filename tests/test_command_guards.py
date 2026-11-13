"""Commands that act for the whole server (or the whole bot) check who runs them."""

import importlib
import sys

from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run
from interactions import Permissions

from extensions.zevent.commands import CommandsMixin as ZeventCommands


def ctx_with(permissions=Permissions.NONE, uid=4):
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    return FakeCtx(guild.add_member(FakeMember(uid, permissions=permissions)), channel)


def test_zevent_finish_defaults_to_manage_guild():
    assert ZeventCommands.end.default_member_permissions == Permissions.MANAGE_GUILD


async def test_zevent_finish_refuses_a_member_without_manage_guild(store_config):
    store_config({"servers": {"1": {"moduleZevent": {"enabled": True}}}})
    ext = bare_extension(ZeventCommands, guilds={})
    ctx = ctx_with()
    await run(ZeventCommands.end, ext, ctx)
    assert "Gérer le serveur" in ctx.last
    assert ext.guilds == {}


async def test_nextvote_is_for_bot_developers_only(store_config, tmp_path):
    # The Spotify package reads its data folder at import.
    store_config(
        {"config": {"misc": {"dataFolder": str(tmp_path)}, "webui": {"developerUserIds": ["42"]}}}
    )
    for name in [m for m in sys.modules if m.startswith("extensions.spotify")]:
        sys.modules.pop(name)
    try:
        votes_mixin = importlib.import_module("extensions.spotify.votes").VotesMixin

        class Votes(votes_mixin):
            def __init__(self):
                self.forced = 0

            async def randomvote(self):
                self.forced += 1

        votes = Votes()
        ctx = ctx_with(Permissions.ADMINISTRATOR)
        await votes_mixin._nextvote(votes, ctx)
        assert votes.forced == 0 and "développeurs" in ctx.last
        await votes_mixin._nextvote(votes, ctx_with(uid=42))
        assert votes.forced == 1
    finally:
        for name in [m for m in sys.modules if m.startswith("extensions.spotify")]:
            sys.modules.pop(name)
