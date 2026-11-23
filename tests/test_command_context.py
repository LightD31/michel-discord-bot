"""What a guild command reads from its context (src/discord_ext/messages.py).

``ctx.guild_id``, ``ctx.guild``, ``ctx.author`` and ``ctx.message`` are typed
optional by interactions.py; a guild command, a button or a menu always has
them. The helpers hand them back typed, and raise on the bug where one is
missing rather than let ``None`` travel on.
"""

import ast
from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeMessage
from interactions import User
from source_scan import parse, python_files, rel

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


# Callbacks that really run outside a guild, by file and function: Secret Santa's
# group-DM sessions and its DM buttons, a Confrérie draft reviewed from the
# owner's DMs, and /ask's answers, which name "Messages privés" for a mention.
READS_A_MAYBE_MISSING_GUILD = {
    "extensions/compareai/ask.py": {"_handle_ask", "_ask_compare"},
    "extensions/confrerie/review.py": {"_draft_guild", "_can_review"},
    "extensions/secretsanta/_common.py": {"get_context_id", "is_guild_admin"},
    "extensions/secretsanta/buttons.py": {"_send_response"},
    "extensions/secretsanta/draws.py": {"draw", "reveal_assignments", "remind_assignment"},
    "extensions/secretsanta/sessions.py": {"create_session", "cancel_session"},
}


def raw_guild_reads(tree: ast.AST) -> list[tuple[str, int, str]]:
    """``ctx.guild_id`` / ``ctx.guild`` read in each function: (function, line, expression)."""
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ("guild_id", "guild")
                and ast.unparse(node.value) in ("ctx", "event.ctx")
            ):
                found.append((fn.name, node.lineno, ast.unparse(node)))
    return found


def test_guild_callbacks_read_their_guild_through_the_helpers():
    """P30: ``guild_id_of(ctx)`` / ``guild_of(ctx)``, typed, and raising on the bug.

    A raw ``ctx.guild_id`` is ``Snowflake | None``: every reader then checked
    for a ``None`` that a guild-scoped command never gets, and some said
    "only in a server" for it.
    """
    offenders = []
    for path in python_files("extensions"):
        allowed = READS_A_MAYBE_MISSING_GUILD.get(rel(path), set())
        for function, line, expression in raw_guild_reads(parse(path)[0]):
            # A function nested in an allowed one is reported under its own name.
            if function not in allowed:
                offenders.append(f"{rel(path)}:{line} {expression} in {function}()")
    assert offenders == [], (
        "read the guild with guild_id_of(ctx) / guild_of(ctx) (src/discord_ext/messages.py); "
        "a callback that runs in DMs goes in READS_A_MAYBE_MISSING_GUILD:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        ("async def f(ctx):\n    return ctx.guild_id\n", True),
        ("async def f(ctx):\n    return str(ctx.guild.id)\n", True),
        ("async def f(event):\n    return event.ctx.guild_id\n", True),
        ("async def f(ctx):\n    return guild_id_of(ctx)\n", False),
        ("async def f(ctx):\n    return guild_of(ctx).name\n", False),
    ],
)
def test_the_scan_catches_raw_reads(source, caught):
    assert bool(raw_guild_reads(ast.parse(source))) is caught
