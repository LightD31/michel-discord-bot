"""A refusal says what to do next (B9): the right channel, the right, who can fix it."""

from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeRole, bare_extension, run
from interactions import Permissions

from extensions.moderation._common import can_moderate
from extensions.spotify import SpotifyExtension
from src.discord_ext.messages import (
    CAN_PURGE,
    author_can,
    module_off,
    permission_names,
    send_error,
)


def test_permissions_are_named_as_discord_names_them():
    assert permission_names(Permissions.MANAGE_MESSAGES) == "« Gérer les messages »"
    assert permission_names(CAN_PURGE) == "« Voir le salon » et « Gérer les messages »"
    assert permission_names(Permissions.NONE) == "les droits nécessaires"


async def test_author_can_names_only_the_missing_right():
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    member = guild.add_member(FakeMember(7, permissions=Permissions.VIEW_CHANNEL))
    ctx = FakeCtx(member, channel)
    assert not await author_can(ctx, channel, CAN_PURGE)
    assert ctx.last == "🔒 Il te faut la permission « Gérer les messages » dans <#10>."
    channel.overrides[7] = Permissions.NONE
    assert not await author_can(ctx, channel, CAN_PURGE)
    assert ctx.last.startswith("🔒 Il te faut les permissions « Voir le salon » et")


async def test_a_refusal_carries_its_own_icon():
    ctx = FakeCtx(FakeMember(7))
    await send_error(ctx, "Pas ici.", icon="🎵")
    await send_error(ctx, "Raté.")
    assert ctx.texts == ["🎵 Pas ici.", "❌ Raté."]


def test_a_module_switched_off_says_who_can_switch_it_on():
    assert module_off("suggestions") == (
        "Le module suggestions n'est pas activé sur ce serveur : un administrateur peut "
        "l'activer dans le tableau de bord de Michel."
    )


def test_a_bot_role_too_low_says_where_to_raise_it():
    guild = FakeGuild(1)
    guild.me.roles = [FakeRole(position=3)]
    actor = guild.add_member(FakeMember(3, roles=[FakeRole(position=10)]))
    target = guild.add_member(FakeMember(7, roles=[FakeRole(position=5)]))
    refusal = can_moderate(actor, target, guild.me)
    assert refusal is not None and "Paramètres du serveur → Rôles" in refusal


@pytest.mark.parametrize("command", ["addsong", "addwithvote"])
async def test_spotify_says_where_its_commands_run(command):
    guild = FakeGuild(1)
    elsewhere = guild.add_channel(FakeChannel(10))
    member = guild.add_member(FakeMember(7))
    ctx = FakeCtx(member, elsewhere)
    ext = bare_extension(
        SpotifyExtension, ctx.bot, get_server=lambda gid: SimpleNamespace(channel_id=42)
    )
    handler = getattr(SpotifyExtension, command)
    await run(handler, ext, ctx, "une chanson")
    assert ctx.last == f"🎵 /{command} se lance dans <#42>."
