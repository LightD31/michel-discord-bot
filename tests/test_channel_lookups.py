"""Every channel the bot looks up for a guild comes from that guild.

``bot.fetch_channel()`` (or the cache's ``bot.get_channel()``) answers for any
channel the bot can see, in any guild:
a channel id copied from another server into a guild's settings (or into what
the bot stored for it) would make the bot post or read there. Lookups go
through ``fetch_guild_channel()`` (``src/discord_ext/channels.py``), which
refuses another guild's channel, or through ``Guild.fetch_channel()`` /
``Guild.get_channel()``, which interactions.py already limits to that guild.
"""

import ast
from types import SimpleNamespace

import pytest
from source_scan import python_files, rel

# The one lookup the others go through.
ALLOWED = {("src/discord_ext/channels.py", "fetch_guild_channel")}


def _receiver(node: ast.expr) -> str:
    """The last name of ``a.b.c`` (``c``), or ``""``."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_guild(name: str) -> bool:
    return "guild" in name.lower() or name in ("server", "srv")


def offenders(tree: ast.AST, where: str) -> list[str]:
    found = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("fetch_channel", "get_channel")
            and not _is_guild(_receiver(node.func.value))
            and (where, function) not in ALLOWED
        ):
            found.append(f"{where}:{node.lineno} in {function}()")
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "")
    return found


def test_channels_are_looked_up_in_their_guild():
    found = []
    for path in python_files("main.py", "src", "extensions"):
        found += offenders(ast.parse(path.read_text(encoding="utf-8")), rel(path))
    assert found == [], "use fetch_guild_channel(bot, guild_id, channel_id): " + ", ".join(found)


@pytest.mark.parametrize(
    "source",
    [
        "async def f(self):\n    await self.bot.fetch_channel(1)",
        "async def f(bot):\n    await bot.fetch_channel(int(x))",
        "async def f(ctx):\n    await ctx.client.fetch_channel(x)",
        "async def f(self):\n    c = await self._client.fetch_channel(x)",
        "async def f(event):\n    await event.bot.fetch_channel(x)",
        "def f(self):\n    return self.bot.get_channel(x)",
    ],
)
def test_the_guard_catches_every_spelling(source):
    assert offenders(ast.parse(source), "x.py")


@pytest.mark.parametrize(
    "source",
    [
        "async def f(guild):\n    await guild.fetch_channel(1)",
        "async def f(self):\n    await self.guild.fetch_channel(1)",
        "async def f(server):\n    await server.fetch_channel(1)",
        "def f(ctx):\n    return ctx.guild.get_channel(1)",
        "async def f(bot):\n    await fetch_guild_channel(bot, 1, 2)",
    ],
)
def test_the_guard_lets_guild_lookups_through(source):
    assert offenders(ast.parse(source), "x.py") == []


# ── fetch_guild_channel itself, and a lookup that now uses it ─────────────


class _Bot:
    def __init__(self, answer):
        self.answer = answer

    async def fetch_channel(self, channel_id):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _discord_down():
    from interactions.client.errors import HTTPException

    return HTTPException(SimpleNamespace(status=503, reason="Service Unavailable"), {})


async def test_a_setting_left_empty_is_no_channel():
    from src.discord_ext.channels import fetch_guild_channel

    # Not asked of Discord at all: the bot would answer an outage.
    assert await fetch_guild_channel(_Bot(_discord_down()), 1, None, raise_outages=True) is None


async def test_an_outage_raises_only_when_asked():
    from src.discord_ext.channels import fetch_guild_channel

    assert await fetch_guild_channel(_Bot(_discord_down()), 1, 10) is None
    with pytest.raises(Exception, match="Service Unavailable"):
        await fetch_guild_channel(_Bot(_discord_down()), 1, 10, raise_outages=True)


@pytest.mark.parametrize("answer", ["not found", "forbidden", None, "other guild"])
async def test_a_gone_or_foreign_channel_is_none_even_when_outages_raise(answer):
    from discord_fakes import discord_error, not_found

    from src.discord_ext.channels import fetch_guild_channel

    channel = {
        "not found": not_found(),
        "forbidden": discord_error(),
        None: None,
        "other guild": SimpleNamespace(_guild_id=2),
    }[answer]
    assert await fetch_guild_channel(_Bot(channel), 1, 10, raise_outages=True) is None


async def test_the_mod_log_is_never_posted_in_another_guild():
    """A mod-log channel id copied from another server: the case stays in its own guild."""
    from discord_fakes import FakeChannel, FakeGuild, bare_extension

    from extensions.moderation import ModerationExtension
    from features.moderation import Infraction
    from src.core import clock

    ours, theirs = FakeGuild(1), FakeGuild(2)
    foreign = theirs.add_channel(FakeChannel(20))
    bot = SimpleNamespace(fetch_channel=_Bot(foreign).fetch_channel)
    ext = bare_extension(ModerationExtension, bot)
    case = Infraction(
        id=1,
        guild_id=str(ours.id),
        user_id="7",
        moderator_id="8",
        type="warn",
        reason="test",
        created_at=clock.now(),
    )
    await ext.log_case({"modLogChannelId": "20"}, case)
    assert foreign.sent == []
