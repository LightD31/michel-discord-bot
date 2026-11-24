"""⭐ Mettre au starboard: staff put a message there by hand, under the same rules."""

import asyncio
from types import SimpleNamespace

import pytest
from discord_fakes import (
    FakeChannel,
    FakeCtx,
    FakeGuild,
    FakeMember,
    FakeMessage,
    bare_extension,
    run,
)
from interactions import Permissions

from extensions.starboard import StarboardExtension


class MemoryStars:
    def __init__(self):
        self.entries = {}

    async def get_by_original(self, original_message_id):
        return self.entries.get(original_message_id)

    async def upsert(self, entry):
        self.entries[entry.original_message_id] = entry


@pytest.fixture
def world(store_config):
    store_config(
        {"servers": {"1": {"moduleStarboard": {"enabled": True, "starboardChannelId": "50"}}}}
    )
    guild = FakeGuild(1)
    guild.default_role = SimpleNamespace(id=1, guild_permissions=Permissions.VIEW_CHANNEL)
    general = guild.add_channel(FakeChannel(10, "général"))
    board = guild.add_channel(FakeChannel(50, "starboard"))
    staff = guild.add_member(
        FakeMember(3, "modo", permissions=Permissions.MANAGE_MESSAGES | Permissions.VIEW_CHANNEL)
    )
    author = guild.add_member(FakeMember(7, "bob"))
    message = FakeMessage(500, content="Quelle soirée !", author=author, channel=general)
    stars = MemoryStars()
    ext = bare_extension(StarboardExtension, None, repository=lambda gid: stars)
    ext.lock = asyncio.Lock()

    def ctx_for(member, channel=general):
        ctx = FakeCtx(member, channel)
        ext.bot = ctx.bot
        ctx.target = message
        return ctx

    return SimpleNamespace(
        ext=ext, guild=guild, general=general, board=board, staff=staff, author=author,
        message=message, stars=stars, ctx=ctx_for,
    )  # fmt: skip


async def test_staff_put_a_message_on_the_starboard(world):
    w = world
    ctx = w.ctx(w.staff)
    await run(StarboardExtension.star_menu, w.ext, ctx)
    (post,) = w.board.sent
    assert post.embeds[0].description == "Quelle soirée !"
    assert "dans <#50>" in ctx.last
    assert w.stars.entries["500"].mirror_message_id == str(post.id)
    # Twice: the link to the one already there.
    again = w.ctx(w.staff)
    await run(StarboardExtension.star_menu, w.ext, again)
    assert len(w.board.sent) == 1 and again.last.startswith("⭐ Déjà au starboard")


async def test_it_needs_manage_messages_where_the_message_is(world):
    w = world
    assert StarboardExtension.star_menu.default_member_permissions == Permissions.MANAGE_MESSAGES
    ctx = w.ctx(w.author)
    await run(StarboardExtension.star_menu, w.ext, ctx)
    assert "« Gérer les messages »" in ctx.last and w.board.sent == []


async def test_a_private_channel_stays_private(world):
    w = world
    w.general.overrides[1] = Permissions.NONE  # @everyone can't see #général
    ctx = w.ctx(w.staff)
    await run(StarboardExtension.star_menu, w.ext, ctx)
    assert w.board.sent == [] and "Accepter les salons privés" in ctx.last
