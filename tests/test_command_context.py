"""What a guild command reads from its context (src/discord_ext/messages.py).

``ctx.guild_id``, ``ctx.guild``, ``ctx.author`` and ``ctx.message`` are typed
optional by interactions.py; a guild command, a button or a menu always has
them. The helpers hand them back typed, and raise on the bug where one is
missing rather than let ``None`` travel on.
"""

from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeMessage
from interactions import User

from src.discord_ext.messages import guild_id_of, guild_of, member_of, message_of


def test_a_guild_command_gets_its_guild_member_and_message():
    guild = FakeGuild(7)
    channel = guild.add_channel(FakeChannel(10))
    member = FakeMember(guild=guild)
    message = FakeMessage(3)
    ctx = FakeCtx(member, channel, message=message)
    assert guild_id_of(ctx) == "7"
    assert guild_of(ctx) is guild
    assert member_of(ctx) is member
    assert message_of(ctx) is message


@pytest.mark.parametrize(
    ("helper", "ctx"),
    [
        (guild_id_of, SimpleNamespace(guild_id=None)),
        (guild_of, SimpleNamespace(guild=None)),
        (member_of, SimpleNamespace(author=None)),
        (message_of, SimpleNamespace(message=None)),
    ],
)
def test_a_missing_part_is_a_bug_raised(helper, ctx):
    with pytest.raises(RuntimeError):
        helper(ctx)


def test_a_plain_user_is_no_member():
    user = User.__new__(User)  # what Discord sends outside a guild
    with pytest.raises(RuntimeError):
        member_of(SimpleNamespace(author=user))
