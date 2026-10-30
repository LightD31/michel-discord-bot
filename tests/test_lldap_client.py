"""LLDAP client: login, GraphQL, token refresh and the LDAP password reset."""

import json
import sys
import types
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from src.integrations import lldap
from src.integrations.lldap import LldapClient, LldapError, LldapSettings, LldapUser

_SECTION = {
    "lldapUrl": "lldap.example.com/",
    "lldapLdapUrl": "lldap.example.com:3890",
    "lldapBaseDn": "dc=example,dc=com",
    "lldapAdminUsername": "admin",
    "lldapAdminPassword": "hunter2",  # pragma: allowlist secret
}


def _settings(**overrides: Any) -> LldapSettings:
    return LldapSettings.from_section({**_SECTION, **overrides})


class _FakeResponse:
    def __init__(self, status: int, body: Any = None):
        self.status = status
        self._body = body

    async def json(self, content_type: str | None = "application/json") -> Any:
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    """Answers each POST with the next queued response (or raises a queued exception)."""

    def __init__(self, *responses: _FakeResponse | Exception):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _client(session: _FakeSession) -> LldapClient:
    async def factory() -> Any:
        return session

    return LldapClient(session_factory=factory)


def _login(token: str = "jwt-1") -> _FakeResponse:
    return _FakeResponse(200, {"token": token, "refreshToken": "refresh"})


def test_settings_normalize_and_derive_dns():
    settings = _settings()
    assert settings.base_url == "https://lldap.example.com"
    assert settings.ldap_url == "ldap://lldap.example.com:3890"
    assert settings.bind_dn == "uid=admin,ou=people,dc=example,dc=com"
    assert settings.user_dn("alice") == "uid=alice,ou=people,dc=example,dc=com"
    assert settings.discord_attribute == "discordid"
    assert settings.configured
    assert "hunter2" not in repr(settings)


def test_settings_unconfigured_without_password():
    assert not _settings(lldapAdminPassword="").configured
    assert not LldapSettings.from_section({}).configured


async def test_login_then_graphql_with_bearer_token():
    session = _FakeSession(_login(), _FakeResponse(200, {"data": {"users": [{"id": "alice"}]}}))
    client = _client(session)

    ids = await client.find_user_ids(_settings(), "email", "alice@example.com")

    assert ids == ["alice"]
    login, query = session.calls
    assert login["url"] == "https://lldap.example.com/auth/simple/login"
    assert login["json"] == {"username": "admin", "password": "hunter2"}  # pragma: allowlist secret
    assert query["url"] == "https://lldap.example.com/api/graphql"
    assert query["headers"] == {"Authorization": "Bearer jwt-1"}
    assert query["json"]["variables"] == {
        "filters": {"eq": {"field": "email", "value": "alice@example.com"}}
    }


async def test_token_is_reused_between_calls():
    ok = {"data": {"users": []}}
    session = _FakeSession(_login(), _FakeResponse(200, ok), _FakeResponse(200, ok))
    client = _client(session)

    await client.find_user_ids(_settings(), "email", "a@example.com")
    await client.find_user_ids(_settings(), "email", "b@example.com")

    assert [c["url"].rsplit("/", 1)[1] for c in session.calls] == ["login", "graphql", "graphql"]


async def test_expired_token_logs_in_again_once():
    session = _FakeSession(
        _login("old"),
        _FakeResponse(401, None),
        _login("new"),
        _FakeResponse(200, {"data": {"users": []}}),
    )
    client = _client(session)

    assert await client.find_user_ids(_settings(), "email", "a@example.com") == []
    assert session.calls[3]["headers"] == {"Authorization": "Bearer new"}


async def test_each_server_keeps_its_own_token():
    ok = {"data": {"users": []}}
    session = _FakeSession(_login("a"), _FakeResponse(200, ok), _login("b"), _FakeResponse(200, ok))
    client = _client(session)

    await client.find_user_ids(_settings(), "email", "x@example.com")
    await client.find_user_ids(
        _settings(lldapUrl="https://other.example"), "email", "x@example.com"
    )

    assert session.calls[2]["url"] == "https://other.example/auth/simple/login"
    assert session.calls[3]["headers"] == {"Authorization": "Bearer b"}


async def test_refused_login_raises():
    client = _client(_FakeSession(_FakeResponse(401, None)))
    with pytest.raises(LldapError, match="identifiants"):
        await client.list_users(_settings())


async def test_graphql_errors_raise_and_flag_conflicts():
    errors = {"errors": [{"message": "UNIQUE constraint failed: users.user_id"}], "data": None}
    client = _client(_FakeSession(_login(), _FakeResponse(200, errors)))
    with pytest.raises(LldapError) as excinfo:
        await client.create_user(
            _settings(), user_id="alice", email="a@example.com", display_name="A", discord_id="1"
        )
    assert excinfo.value.conflict


async def test_create_user_sends_discord_attribute():
    session = _FakeSession(_login(), _FakeResponse(200, {"data": {"createUser": {"id": "alice"}}}))
    await _client(session).create_user(
        _settings(lldapDiscordAttribute="DiscordId"),
        user_id="alice",
        email="a@example.com",
        display_name="Alice",
        discord_id="42",
    )
    assert session.calls[1]["json"]["variables"]["user"] == {
        "id": "alice",
        "email": "a@example.com",
        "displayName": "Alice",
        "attributes": [{"name": "discordid", "value": ["42"]}],
    }


async def test_unconfirmed_mutation_raises():
    session = _FakeSession(
        _login(), _FakeResponse(200, {"data": {"addUserToGroup": {"ok": False}}})
    )
    with pytest.raises(LldapError):
        await _client(session).add_to_group(_settings(), "alice", 3)


async def test_unreachable_server_raises():
    client = _client(_FakeSession(ClientConnectionError("boom")))
    with pytest.raises(LldapError, match="injoignable"):
        await client.list_users(_settings())


async def test_unconfigured_settings_never_call_out():
    session = _FakeSession()
    with pytest.raises(LldapError, match="pas configuré"):
        await _client(session).list_users(LldapSettings.from_section({}))
    assert session.calls == []


async def test_list_users_parses_discord_ids_and_groups():
    data = {
        "users": [
            {
                "id": "alice",
                "attributes": [
                    {"name": "mail", "value": ["a@example.com"]},
                    {"name": "discordid", "value": ["42"]},
                ],
                "groups": [{"id": 3}, {"id": 4}],
            },
            {"id": "admin", "attributes": [], "groups": [{"id": 1}]},
            {"id": "blank", "attributes": [{"name": "discordid", "value": [" "]}], "groups": []},
        ]
    }
    session = _FakeSession(_login(), _FakeResponse(200, {"data": data}))

    users = await _client(session).list_users(_settings())

    assert users == [
        LldapUser("alice", "42", frozenset({3, 4})),
        LldapUser("admin", None, frozenset({1})),
        LldapUser("blank", None, frozenset()),
    ]


class _FakeLdapConnection:
    instances: list["_FakeLdapConnection"] = []
    bind_ok = True
    modify_ok = True

    def __init__(self, server: Any, user: str, password: str, receive_timeout: int):
        self.server = server
        self.user = user
        self.password = password
        self.result = {"description": "invalidCredentials"}
        self.unbound = False
        self.modified: tuple[Any, ...] | None = None
        self.extend = types.SimpleNamespace(
            standard=types.SimpleNamespace(modify_password=self._modify_password)
        )
        _FakeLdapConnection.instances.append(self)

    def bind(self) -> bool:
        return self.bind_ok

    def _modify_password(self, user_dn: str, old: Any, new: str) -> bool:
        self.modified = (user_dn, old, new)
        return self.modify_ok

    def unbind(self) -> None:
        self.unbound = True


@pytest.fixture
def fake_ldap3(monkeypatch):
    class LDAPException(Exception):
        pass

    module = types.ModuleType("ldap3")
    module.Server = lambda url, connect_timeout: ("server", url)  # type: ignore[attr-defined]
    module.Connection = _FakeLdapConnection  # type: ignore[attr-defined]
    exceptions = types.ModuleType("ldap3.core.exceptions")
    exceptions.LDAPException = LDAPException  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ldap3", module)
    monkeypatch.setitem(sys.modules, "ldap3.core", types.ModuleType("ldap3.core"))
    monkeypatch.setitem(sys.modules, "ldap3.core.exceptions", exceptions)
    _FakeLdapConnection.instances = []
    monkeypatch.setattr(_FakeLdapConnection, "bind_ok", True)
    monkeypatch.setattr(_FakeLdapConnection, "modify_ok", True)
    return _FakeLdapConnection


async def test_set_password_binds_as_admin_and_resets(fake_ldap3):
    await LldapClient().set_password(_settings(), "alice", "s3cret")  # pragma: allowlist secret

    (conn,) = fake_ldap3.instances
    assert conn.server == ("server", "ldap://lldap.example.com:3890")
    assert conn.user == "uid=admin,ou=people,dc=example,dc=com"
    assert conn.modified == ("uid=alice,ou=people,dc=example,dc=com", None, "s3cret")
    assert conn.unbound


async def test_set_password_refused_raises_and_unbinds(fake_ldap3):
    fake_ldap3.modify_ok = False
    with pytest.raises(LldapError, match="mot de passe"):
        await LldapClient().set_password(_settings(), "alice", "s3cret")  # pragma: allowlist secret
    assert fake_ldap3.instances[0].unbound


async def test_set_password_bad_admin_credentials_raise(fake_ldap3):
    fake_ldap3.bind_ok = False
    with pytest.raises(LldapError, match="administrateur"):
        await LldapClient().set_password(_settings(), "alice", "s3cret")  # pragma: allowlist secret
    assert fake_ldap3.instances[0].modified is None


def test_conflict_detection():
    assert LldapError("LLDAP a refusé la requête : UNIQUE constraint failed").conflict
    assert not LldapError("LLDAP injoignable").conflict
    assert lldap.DEFAULT_DISCORD_ATTRIBUTE == "discordid"
