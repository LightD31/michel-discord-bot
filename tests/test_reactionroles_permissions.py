"""Role-menu privilege rules: pure rules, the interactions adapter, and the dashboard routes.

Regression for the dashboard escalation where a ``MANAGE_GUILD``-only member
could publish a self-service menu granting any role — Administrator included —
and click it.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.webui.routes.rolemenus as rolemenus_routes
from features.reactionroles import (
    MenuAuthor,
    RoleInfo,
    RoleMenuEntry,
    access_refusal,
    mention_refusal,
    role_menu_refusal,
    self_assign_refusal,
)
from src.core.permissions import (
    ADMINISTRATOR,
    BAN_MEMBERS,
    MANAGE_GUILD,
    MANAGE_MESSAGES,
    MANAGE_ROLES,
    MENTION_EVERYONE,
)
from src.discord_ext.rolemenus import entries_refusal, menu_author
from src.webui.auth import DiscordOAuth, Session
from src.webui.context import WebUIContext

BOT_TOP = 50


def role(position: int, permissions: int = 0, **kw) -> RoleInfo:
    return RoleInfo(id=str(position), position=position, permissions=permissions, **kw)


# ── Pure rules ──────────────────────────────────────────────────────────


def test_privileged_roles_are_refused_even_for_the_owner():
    owner = MenuAuthor(permissions=0, top_position=0, is_owner=True)
    for flag in (ADMINISTRATOR, BAN_MEMBERS, MANAGE_ROLES, MANAGE_MESSAGES):
        assert role_menu_refusal(role(10, flag), author=owner, bot_top_position=BOT_TOP)


def test_manage_guild_alone_cannot_hand_out_roles():
    """What the dashboard lets in (MANAGE_GUILD) is not enough to grant a role."""
    admin = MenuAuthor(permissions=MANAGE_GUILD, top_position=40)
    refusal = role_menu_refusal(role(10), author=admin, bot_top_position=BOT_TOP)
    assert refusal and "Gérer les rôles" in refusal


def test_roles_must_sit_below_the_authors_highest_role():
    mod = MenuAuthor(permissions=MANAGE_ROLES, top_position=20)
    assert role_menu_refusal(role(19), author=mod, bot_top_position=BOT_TOP) is None
    assert role_menu_refusal(role(20), author=mod, bot_top_position=BOT_TOP)  # their own top role
    assert role_menu_refusal(role(30), author=mod, bot_top_position=BOT_TOP)


def test_administrator_counts_as_manage_roles():
    admin = MenuAuthor(permissions=ADMINISTRATOR, top_position=40)
    assert role_menu_refusal(role(10), author=admin, bot_top_position=BOT_TOP) is None


def test_owner_and_developer_skip_the_hierarchy_but_not_the_bot():
    owner = MenuAuthor(permissions=0, top_position=0, is_owner=True)
    dev = MenuAuthor(permissions=0, top_position=0, bypass_hierarchy=True)
    for author in (owner, dev):
        assert role_menu_refusal(role(45), author=author, bot_top_position=BOT_TOP) is None
        assert role_menu_refusal(role(BOT_TOP), author=author, bot_top_position=BOT_TOP)


def test_everyone_and_integration_roles_are_refused():
    owner = MenuAuthor(permissions=0, top_position=0, is_owner=True)
    assert role_menu_refusal(role(0, is_default=True), author=owner, bot_top_position=BOT_TOP)
    assert role_menu_refusal(role(5, managed=True), author=owner, bot_top_position=BOT_TOP)


def test_self_assign_refusal_only_looks_at_permissions():
    assert self_assign_refusal(role(10)) is None
    assert self_assign_refusal(role(10, ADMINISTRATOR))


# ── interactions adapter ────────────────────────────────────────────────


class FakeRole:
    def __init__(self, role_id: int, position: int, permissions: int = 0, managed=False):
        self.id = role_id
        self.name = f"role-{role_id}"
        self.position = position
        self.permissions = permissions
        self.managed = managed
        self.color = None


class FakeMember:
    def __init__(self, user_id: int, top: FakeRole, permissions: int):
        self.id = user_id
        self.top_role = top
        self.guild_permissions = permissions


class FakeGuild:
    def __init__(self, members: dict[int, FakeMember], bot_member: FakeMember, owner_id=1):
        self.id = 1000
        everyone = FakeRole(1000, 0)
        self.roles = [
            everyone,
            FakeRole(10, 10),  # cosmetic
            FakeRole(20, 20),  # mod's own top role
            FakeRole(30, 30, permissions=ADMINISTRATOR),  # Admin
            FakeRole(35, 35),  # above the mod, no permission (private channels)
        ]
        self.members = members
        self.me = bot_member
        self.owner_id = owner_id

    async def fetch_member(self, user_id):
        return self.members.get(int(user_id))

    def is_owner(self, user_id) -> bool:
        return int(user_id) == self.owner_id


def entries(*role_ids: int) -> list[RoleMenuEntry]:
    return [RoleMenuEntry(role_id=str(r), emoji="🎁", label=f"r{r}") for r in role_ids]


def make_guild(mod_permissions: int) -> FakeGuild:
    bot_member = FakeMember(999, FakeRole(50, BOT_TOP), ADMINISTRATOR)
    mod_top = FakeRole(20, 20)
    return FakeGuild({7: FakeMember(7, mod_top, mod_permissions)}, bot_member)


def test_entries_refusal_names_the_offending_entry():
    guild = make_guild(MANAGE_ROLES)
    author = menu_author(guild, guild.members[7])
    assert entries_refusal(guild, entries(10), author=author, bot_position=BOT_TOP) is None
    refusal = entries_refusal(guild, entries(10, 30), author=author, bot_position=BOT_TOP)
    assert refusal and refusal.startswith("Entrée 2")
    assert entries_refusal(guild, entries(10, 35), author=author, bot_position=BOT_TOP)
    missing = entries_refusal(guild, entries(77), author=author, bot_position=BOT_TOP)
    assert missing and "introuvable" in missing


# ── Dashboard routes ────────────────────────────────────────────────────


class FakeChannel:
    def __init__(self, guild_id: int = 1000):
        self.id = 555
        self._guild_id = guild_id  # the FakeGuild's: a channel of that server
        self.sent: list = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=777)


class FakeBot:
    def __init__(self, guild: FakeGuild):
        self.guild = guild
        self.guilds = [guild]
        self.user = SimpleNamespace(id=999)
        self.channel = FakeChannel()

    def get_guild(self, guild_id):
        return self.guild if int(guild_id) == self.guild.id else None

    async def fetch_guild(self, guild_id):
        return self.get_guild(guild_id)

    async def fetch_channel(self, channel_id):
        return self.channel


class StubRepo:
    added: list = []

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def add(self, menu):
        type(self).added.append(menu)
        return "a" * 24

    async def update(self, menu_id, **fields):
        return None

    async def delete(self, menu_id):
        return None


@pytest.fixture
def stub_repo(monkeypatch):
    StubRepo.added = []
    monkeypatch.setattr(rolemenus_routes, "ReactionRolesRepository", StubRepo)
    return StubRepo


def make_ctx(guild: FakeGuild, user_id: str = "7") -> tuple[WebUIContext, Session]:
    oauth = DiscordOAuth(client_id="", client_secret="", redirect_uri="")
    ctx = WebUIContext(bot=FakeBot(guild), bot_loop=None, oauth=oauth)
    session = Session(user_id=user_id, username="mod", avatar=None, guilds=[], expires_at=9e9)
    oauth.sessions[session.token_hash] = session
    return ctx, session


def endpoint(ctx: WebUIContext, path: str, method: str):
    router = rolemenus_routes.create_router(ctx)
    return next(r for r in router.routes if r.path == path and method in r.methods).endpoint


def run(ctx: WebUIContext, coro_factory):
    async def main():
        ctx.bot_loop = asyncio.get_running_loop()
        return await coro_factory()

    return asyncio.run(main())


def request_for(session: Session):
    return SimpleNamespace(cookies={"michel_session": session.session_token})


def create(ctx, session, role_ids):
    create_menu = endpoint(ctx, "/api/servers/{server_id}/rolemenus", "POST")
    body = rolemenus_routes.RoleMenuCreate(
        channel_id="555",
        title="Rôles",
        entries=[
            rolemenus_routes.RoleMenuEntryIn(role_id=str(r), emoji="🎁", label=f"r{r}")
            for r in role_ids
        ],
    )
    return run(ctx, lambda: create_menu(request_for(session), "1000", body))


def test_manage_guild_member_cannot_publish_an_admin_role(stub_repo):
    """The reported escalation: MANAGE_GUILD only, Administrator role in the menu."""
    ctx, session = make_ctx(make_guild(MANAGE_GUILD))
    with pytest.raises(HTTPException) as exc:
        create(ctx, session, [30])
    assert exc.value.status_code == 400
    assert stub_repo.added == []
    assert ctx.bot.channel.sent == []


def test_manage_guild_member_cannot_publish_even_a_cosmetic_role(stub_repo):
    ctx, session = make_ctx(make_guild(MANAGE_GUILD))
    with pytest.raises(HTTPException) as exc:
        create(ctx, session, [10])
    assert exc.value.status_code == 400
    assert "Gérer les rôles" in exc.value.detail


def test_role_manager_publishes_roles_below_their_own(stub_repo):
    ctx, session = make_ctx(make_guild(MANAGE_GUILD | MANAGE_ROLES))
    resp = create(ctx, session, [10])
    assert json.loads(resp.body)["status"] == "ok"
    assert len(stub_repo.added) == 1 and len(ctx.bot.channel.sent) == 1

    # A permission-less role above them (private channels) is still refused.
    with pytest.raises(HTTPException):
        create(ctx, session, [35])


def test_role_picker_lists_only_what_the_requester_may_use():
    ctx, session = make_ctx(make_guild(MANAGE_GUILD | MANAGE_ROLES))
    list_roles = endpoint(ctx, "/api/servers/{server_id}/roles", "GET")
    resp = run(ctx, lambda: list_roles(request_for(session), "1000"))
    assert [r["id"] for r in json.loads(resp.body)["roles"]] == ["10"]


def test_click_refuses_a_role_that_became_privileged():
    """A role edited to carry ADMINISTRATOR after the menu was posted is not handed out."""
    from extensions.reactionroles.buttons import ButtonsMixin

    granted: list = []
    errors: list = []
    admin_role = FakeRole(30, 30, permissions=ADMINISTRATOR)

    menu = SimpleNamespace(entries=entries(30))

    class Repo:
        async def get(self, menu_id):
            return menu

    member = SimpleNamespace(id=7, roles=[], add_role=lambda *a, **k: granted.append(a))
    ctx = SimpleNamespace(
        guild=SimpleNamespace(get_role=lambda rid: admin_role if int(rid) == 30 else None),
        guild_id=1000,
        custom_id=f"rrole:{'a' * 24}:0",
        author=member,
    )
    mixin = ButtonsMixin()
    mixin.repository = lambda guild_id: Repo()

    async def fake_send_error(_ctx, message):
        errors.append(message)

    import extensions.reactionroles.buttons as buttons

    original = buttons.send_error
    buttons.send_error = fake_send_error
    try:
        asyncio.run(ButtonsMixin.on_role_button.callback(mixin, ctx))
    finally:
        buttons.send_error = original
    assert granted == []
    assert errors and "modération" in errors[0]


def test_mention_refusal_follows_mentionable_and_mention_everyone():
    member = MenuAuthor(permissions=MANAGE_GUILD, top_position=5)
    assert mention_refusal(role(10, mentionable=True), author=member) is None
    assert "Mentionner" in mention_refusal(role(10), author=member)
    pinger = MenuAuthor(permissions=MANAGE_GUILD | MENTION_EVERYONE, top_position=5)
    assert mention_refusal(role(10), author=pinger) is None
    assert mention_refusal(role(0, is_default=True, mentionable=True), author=pinger)


def test_access_refusal_is_the_hierarchy_rule_alone():
    mod = MenuAuthor(permissions=MANAGE_ROLES, top_position=20)
    assert access_refusal(role(10, ADMINISTRATOR), author=mod, purpose="x") is None
    assert access_refusal(role(25), author=mod, purpose="x")
    assert "Gérer les rôles" in access_refusal(
        role(1), author=MenuAuthor(permissions=MANAGE_GUILD, top_position=20), purpose="x"
    )
    assert access_refusal(role(0, is_default=True), author=mod, purpose="x")
