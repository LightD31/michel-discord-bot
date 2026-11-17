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


async def test_nextvote_is_for_bot_developers_only(store_config):
    store_config({"config": {"webui": {"developerUserIds": ["42"]}}})
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


# ── Default permissions: one flag, not ADMINISTRATOR | X ──────────────────


def _default_permissions(command):
    return command.default_member_permissions


def test_moderators_see_the_commands_meant_for_them(store_config):
    """``ADMINISTRATOR | MANAGE_MESSAGES`` reads as "both": only admins saw them (Q4)."""
    from extensions.admin import AdminExtension
    from extensions.help import _member_can_use
    from extensions.reactionroles.commands import CommandsMixin as RoleMenus

    moderator = FakeMember(7, permissions=Permissions.MANAGE_MESSAGES)
    for command in (AdminExtension.delete, AdminExtension.send):
        assert _member_can_use(moderator)(_default_permissions(command)), command.name
    role_manager = FakeMember(8, permissions=Permissions.MANAGE_ROLES)
    for command in (
        RoleMenus.rolemenu_create,
        RoleMenus.rolemenu_list,
        RoleMenus.rolemenu_delete,
        RoleMenus.rolemenu_edit,
    ):
        assert _member_can_use(role_manager)(_default_permissions(command)), command.name
    assert not _member_can_use(FakeMember(9))(_default_permissions(AdminExtension.delete))


class MemoryMenus:
    def __init__(self):
        from features.reactionroles import RoleMenu
        from src.core import clock

        menu = RoleMenu(
            guild_id="1",
            channel_id="10",
            title="Jeux",
            entries=[],
            created_by="5",
            created_at=clock.now(),
        )
        self.menus = {"m1": menu}
        self.updates = []

    async def get(self, menu_id):
        return self.menus.get(menu_id)

    async def delete(self, menu_id):
        self.menus.pop(menu_id)

    async def update(self, menu_id, **fields):
        self.updates.append((menu_id, fields))


async def test_rolemenu_delete_and_edit_check_their_author_at_run_time(store_config):
    """A server can open a command to everyone: deleting a menu still needs « Gérer les rôles »."""
    from extensions.reactionroles.commands import CommandsMixin as RoleMenus

    store_config({"servers": {"1": {"moduleReactionRoles": {"enabled": True}}}})
    repo = MemoryMenus()
    ext = bare_extension(RoleMenus, repository=lambda gid: repo)
    ctx = ctx_with()
    await run(RoleMenus.rolemenu_delete, ext, ctx, menu_id="m1")
    assert "Gérer les rôles" in str(ctx.replies[-1])
    await run(RoleMenus.rolemenu_edit, ext, ctx, menu_id="m1", title="Pirate")
    assert "m1" in repo.menus and repo.updates == []

    await run(RoleMenus.rolemenu_delete, ext, ctx_with(Permissions.MANAGE_ROLES), menu_id="m1")
    assert "m1" not in repo.menus
