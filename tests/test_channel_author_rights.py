"""Commands that make the bot post in a channel check the author could post there (S13)."""

from types import SimpleNamespace

from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeRole, bare_extension, run
from interactions import Permissions

from extensions.reactionroles import ReactionRolesExtension
from extensions.uptime import UptimeExtension

STAFF = Permissions.MANAGE_ROLES | Permissions.SEND_MESSAGES | Permissions.VIEW_CHANNEL


def _setup(store_config, module: str):
    store_config({"servers": {"1": {module: {"enabled": True}}}})
    guild = FakeGuild(1)
    role = guild.add_role(FakeRole(301, position=2))
    author = guild.add_member(FakeMember(7, roles=[FakeRole(302, position=10)], permissions=STAFF))
    here = guild.add_channel(FakeChannel(10))
    locked = guild.add_channel(FakeChannel(11))
    locked.overrides[author.id] = Permissions.VIEW_CHANNEL  # can read, can't post
    return guild, role, author, here, locked


class RoleList(dict):
    """``guild.roles`` as Discord gives it (a list of roles), still keyed for ``get_role``."""

    def __iter__(self):
        return iter(list(self.values()))


class Repo:
    def __init__(self):
        self.added = []

    async def add(self, menu):
        self.added.append(menu)
        return "a" * 24

    async def update(self, menu_id, **fields):
        return None


async def test_rolemenu_create_refuses_a_channel_the_author_cannot_post_in(store_config):
    guild, role, author, here, locked = _setup(store_config, "moduleReactionRoles")
    guild.roles = RoleList(guild.roles)
    repo = Repo()
    ctx = FakeCtx(author, here)
    ext = bare_extension(ReactionRolesExtension, ctx.bot, repository=lambda guild_id: repo)
    await run(ReactionRolesExtension.rolemenu_create, ext, ctx, locked, "Rôles", "301,🎁,Jeux")
    assert repo.added == [] and locked.sent == []
    assert "droits nécessaires" in ctx.last


async def test_rolemenu_create_posts_where_the_author_can_post(store_config):
    guild, role, author, here, _ = _setup(store_config, "moduleReactionRoles")
    guild.roles = RoleList(guild.roles)
    repo = Repo()
    ctx = FakeCtx(author, here)
    ext = bare_extension(ReactionRolesExtension, ctx.bot, repository=lambda guild_id: repo)
    await run(ReactionRolesExtension.rolemenu_create, ext, ctx, here, "Rôles", "301,🎁,Jeux")
    assert len(repo.added) == 1 and len(here.sent) == 1


async def test_an_uptime_alert_needs_the_right_to_post_in_its_channel(store_config):
    guild, _, author, here, locked = _setup(store_config, "moduleUptime")
    ctx = FakeCtx(author, here)
    ext = bare_extension(
        UptimeExtension, ctx.bot, kuma_ok=True, monitors={5: SimpleNamespace(name="NAS")}
    )
    await run(UptimeExtension.setup_maintenance_alert, ext, ctx, "5", locked)
    assert "droits nécessaires" in ctx.last


async def test_the_twitch_planning_needs_the_right_to_post_and_pin(store_config, monkeypatch):
    import extensions.twitch as twitch_module
    from extensions.twitch import TwitchExtension

    guild, _, author, here, _ = _setup(store_config, "moduleTwitch")
    saved = []
    monkeypatch.setattr(twitch_module, "_save_streamer_planning", lambda *a: saved.append(a))
    ctx = FakeCtx(author, here)
    ext = bare_extension(TwitchExtension, ctx.bot, streamers={"1_zerator": SimpleNamespace()})
    # STAFF may post in #10 but not pin (no MANAGE_MESSAGES).
    await run(TwitchExtension.twitch_planning_setup, ext, ctx, "zerator", here, pin=True)
    assert saved == [] and "droits nécessaires" in ctx.last
