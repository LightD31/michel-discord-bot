"""LLDAP extension: the role sync over the guild cache and the /register flow."""

import asyncio
from typing import Any

import pytest

from extensions.lldap import _common, commands, sync
from extensions.lldap.commands import CommandsMixin
from extensions.lldap.sync import SyncMixin
from src.integrations.lldap import LldapError, LldapUser

GUILD_A = "111"
GUILD_B = "222"
ROLE = 900
OTHER_ROLE = 901
GROUP = 3


def _module(**overrides: Any) -> dict[str, Any]:
    return {
        "enabled": True,
        "lldapUrl": "https://lldap.example.com",
        "lldapLdapUrl": "ldap://lldap.example.com:3890",
        "lldapBaseDn": "dc=example,dc=com",
        "lldapAdminUsername": "admin",
        "lldapAdminPassword": "hunter2",  # pragma: allowlist secret
        "lldapRoleGroups": {str(ROLE): str(GROUP)},
        **overrides,
    }


class _Member:
    def __init__(self, member_id: int, *roles: int, username: str = "alice", bot: bool = False):
        self.id = member_id
        self.roles = set(roles)
        self.username = username
        self.display_name = username.title()
        self.bot = bot

    def has_role(self, *roles: int) -> bool:
        return any(int(role) in self.roles for role in roles)


class _Guild:
    def __init__(self, guild_id: str, members: list[_Member], roles=(ROLE,), chunked=True):
        self.id = int(guild_id)
        self.members = members
        self._roles = set(roles)
        self.chunked = asyncio.Event()
        if chunked:
            self.chunked.set()
        self.chunk_calls: list[dict[str, Any]] = []

    def get_role(self, role_id: int):
        return object() if role_id in self._roles else None

    async def gateway_chunk(self, **kwargs: Any) -> None:
        self.chunk_calls.append(kwargs)
        self.chunked.set()


class _Bot:
    def __init__(self, *guilds: _Guild):
        self.guilds = {str(g.id): g for g in guilds}

    def get_guild(self, guild_id: int):
        return self.guilds.get(str(guild_id))


class _FakeLldap:
    def __init__(self, users: list[LldapUser] | None = None):
        self.users = users or []
        self.calls: list[tuple[Any, ...]] = []
        self.linked: list[str] = []
        self.emails: list[str] = []
        self.fail_on: str | None = None

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise LldapError(f"{name} failed")

    async def list_users(self, settings):
        self.calls.append(("list_users", settings.instance_key))
        self._maybe_fail("list_users")
        return self.users

    async def add_to_group(self, settings, user_id, group_id):
        self.calls.append(("add", user_id, group_id))
        self._maybe_fail("add_to_group")

    async def remove_from_group(self, settings, user_id, group_id):
        self.calls.append(("remove", user_id, group_id))

    async def find_user_ids(self, settings, field, value):
        self.calls.append(("find", field, value))
        return self.linked if field == settings.discord_attribute else self.emails

    async def create_user(self, settings, **kwargs):
        self.calls.append(("create", kwargs))
        self._maybe_fail("create_user")

    async def set_password(self, settings, user_id, password):
        self.calls.append(("set_password", user_id))
        self._maybe_fail("set_password")

    async def delete_user(self, settings, user_id):
        self.calls.append(("delete", user_id))


class _Harness(CommandsMixin, SyncMixin):
    def __init__(self, bot: _Bot, client: _FakeLldap):
        self.bot = bot
        self.lldap = client
        self._sync_lock = asyncio.Lock()
        self._register_lock = asyncio.Lock()
        self._failing: set[str] = set()
        self._warned: set[str] = set()


@pytest.fixture
def guilds(monkeypatch):
    """Install per-guild module configs: ``guilds({GUILD_A: _module()})``."""

    def install(configs: dict[str, dict[str, Any]]) -> None:
        monkeypatch.setattr(_common, "module_config", configs)
        enabled = [g for g, c in configs.items() if c.get("enabled")]
        monkeypatch.setattr(sync, "enabled_servers", enabled)
        monkeypatch.setattr(commands, "enabled_servers", enabled)

    return install


def _users() -> list[LldapUser]:
    return [
        LldapUser("alice", "1", frozenset()),  # holds the role, not in the group
        LldapUser("bob", "2", frozenset({GROUP})),  # lost the role
        LldapUser("admin", None, frozenset({GROUP})),  # unlinked: never touched
    ]


# ── Sync ─────────────────────────────────────────────────────────────


async def test_sync_adds_holders_and_removes_former_ones(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap(_users())
    bot = _Bot(_Guild(GUILD_A, [_Member(1, ROLE), _Member(2), _Member(3, ROLE, bot=True)]))

    report = await _Harness(bot, client).run_sync()

    assert ("add", "alice", GROUP) in client.calls
    assert ("remove", "bob", GROUP) in client.calls
    assert not any(c[0] in ("add", "remove") and c[1] == "admin" for c in client.calls)
    assert (report.added, report.removed, report.failed) == (1, 1, 0)


async def test_missing_role_blocks_its_group(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap(_users())
    bot = _Bot(_Guild(GUILD_A, [_Member(1, ROLE)], roles=()))

    report = await _Harness(bot, client).run_sync()

    assert report.blocked_groups == {GROUP}
    assert client.calls == []


async def test_unavailable_guild_blocks_its_groups(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap(_users())

    report = await _Harness(_Bot(), client).run_sync()

    assert report.blocked_groups == {GROUP}
    assert client.calls == []


async def test_unchunked_guild_is_chunked_without_presences(guilds):
    guilds({GUILD_A: _module()})
    guild = _Guild(GUILD_A, [_Member(1, ROLE)], chunked=False)

    await _Harness(_Bot(guild), _FakeLldap(_users())).run_sync()

    assert guild.chunk_calls == [{"presences": False}]


async def test_guilds_sharing_a_server_are_merged(guilds):
    guilds(
        {
            GUILD_A: _module(),
            GUILD_B: _module(lldapRoleGroups={str(OTHER_ROLE): str(GROUP)}),
        }
    )
    client = _FakeLldap(_users())
    bot = _Bot(
        _Guild(GUILD_A, [_Member(1, ROLE)]),
        # bob holds the role mapped to the same group in the other guild
        _Guild(GUILD_B, [_Member(2, OTHER_ROLE)], roles=(OTHER_ROLE,)),
    )

    # A manual sync from guild A still takes guild B's holders into account.
    report = await _Harness(bot, client).run_sync([GUILD_A])

    assert ("remove", "bob", GROUP) not in client.calls
    assert client.calls.count(("list_users", "https://lldap.example.com")) == 1
    assert (report.added, report.removed) == (1, 0)


async def test_unreachable_server_is_reported(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap(_users())
    client.fail_on = "list_users"

    report = await _Harness(_Bot(_Guild(GUILD_A, [_Member(1, ROLE)])), client).run_sync()

    assert report.errors == ["list_users failed"]


async def test_unconfigured_guild_is_skipped(guilds):
    guilds({GUILD_A: _module(lldapAdminPassword="")})
    client = _FakeLldap(_users())

    await _Harness(_Bot(_Guild(GUILD_A, [_Member(1, ROLE)])), client).run_sync()

    assert client.calls == []


# ── /register ────────────────────────────────────────────────────────


class _Ctx:
    def __init__(self, guild_id: str, author: _Member):
        self.guild = type("G", (), {"id": int(guild_id)})()
        self.author = author
        self.sent: list[dict[str, Any]] = []
        self.deferred = False

    async def defer(self, ephemeral: bool = False) -> None:
        self.deferred = True

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        self.sent.append({"content": content, **kwargs})


async def _register(harness: _Harness, ctx: _Ctx, email: str, username: str | None = None):
    await CommandsMixin.register_command.callback(harness, ctx, email=email, username=username)


async def test_register_creates_the_account(guilds):
    guilds({GUILD_A: _module(lldapServiceName="Jellyfin")})
    client = _FakeLldap()
    ctx = _Ctx(GUILD_A, _Member(42, ROLE, username="Alice.Smith"))

    await _register(_Harness(_Bot(), client), ctx, " Alice@Example.com ")

    create = next(c for c in client.calls if c[0] == "create")
    assert create[1] == {
        "user_id": "alice.smith",
        "email": "alice@example.com",
        "display_name": "Alice.Smith",
        "discord_id": "42",
    }
    assert ("set_password", "alice.smith") in client.calls
    assert ("add", "alice.smith", GROUP) in client.calls
    (reply,) = ctx.sent
    embed = reply["embeds"][0]
    assert reply["ephemeral"] is True
    assert embed.title == "Compte Jellyfin créé"
    assert [f.name for f in embed.fields] == [
        "Nom d'utilisateur",
        "Mot de passe temporaire",
        "Connexion",
    ]
    assert embed.fields[2].value == "https://lldap.example.com"


async def test_register_requires_a_mapped_role(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap()
    ctx = _Ctx(GUILD_A, _Member(42))

    await _register(_Harness(_Bot(), client), ctx, "a@example.com")

    assert f"<@&{ROLE}>" in ctx.sent[0]["content"]
    assert client.calls == []


async def test_register_refuses_an_already_linked_member(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap()
    client.linked = ["alice"]
    ctx = _Ctx(GUILD_A, _Member(42, ROLE))

    await _register(_Harness(_Bot(), client), ctx, "a@example.com", "alice2")

    assert ctx.sent[0]["content"] == f"❌ {_common.ALREADY_LINKED}"
    assert not any(c[0] == "create" for c in client.calls)


async def test_register_rolls_back_when_the_password_fails(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap()
    client.fail_on = "set_password"
    ctx = _Ctx(GUILD_A, _Member(42, ROLE))

    await _register(_Harness(_Bot(), client), ctx, "a@example.com", "alice")

    assert ("delete", "alice") in client.calls
    assert not any(c[0] == "add" for c in client.calls)
    assert ctx.sent[0]["content"].startswith("❌ Impossible de finaliser le compte")


async def test_register_rejects_a_bad_username(guilds):
    guilds({GUILD_A: _module()})
    client = _FakeLldap()
    ctx = _Ctx(GUILD_A, _Member(42, ROLE))

    await _register(_Harness(_Bot(), client), ctx, "a@example.com", "no spaces")

    assert ctx.sent[0]["content"] == f"❌ {_common.BAD_USERNAME}"
    assert client.calls == []


async def test_register_in_a_disabled_guild(guilds):
    guilds({GUILD_A: _module(enabled=False)})
    ctx = _Ctx(GUILD_A, _Member(42, ROLE))

    await _register(_Harness(_Bot(), _FakeLldap()), ctx, "a@example.com")

    assert ctx.sent[0]["content"] == f"❌ {_common.NOT_ENABLED}"
