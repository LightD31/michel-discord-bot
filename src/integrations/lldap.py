"""LLDAP client (no Discord imports).

`LLDAP <https://github.com/lldap/lldap>`_ manages its users and groups through
a GraphQL API (``/api/graphql``, JWT from ``/auth/simple/login``), but that API
cannot set a password: passwords go through the LDAP Password Modify extended
operation instead, done here with ``ldap3`` in a worker thread.

Each Discord server points at its own LLDAP server (``moduleLldap``), so the
settings are passed to every call and the client keeps one JWT per server.
The Discord id of the member who created an account lives in a custom user
attribute (``discordid`` by default) that has to exist in LLDAP's user schema.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientError, ClientSession

from src.core import logging as logutil
from src.core.errors import IntegrationError
from src.core.http import http_client

logger = logutil.init_logger(__name__)

DEFAULT_DISCORD_ATTRIBUTE = "discordid"
LDAP_TIMEOUT_SECONDS = 10

# LLDAP reports a duplicate user id or email as a database constraint error.
_CONFLICT_MARKERS = ("unique", "already exists", "duplicate")


class LldapError(IntegrationError):
    """Raised when an LLDAP call fails. Messages never include a password."""

    @property
    def conflict(self) -> bool:
        """True when LLDAP refused the change because the entry already exists."""
        text = str(self).lower()
        return any(marker in text for marker in _CONFLICT_MARKERS)


def _normalize_url(value: str, default_scheme: str) -> str:
    text = value.strip().rstrip("/")
    if text and "://" not in text:
        text = f"{default_scheme}://{text}"
    return text


@dataclass(frozen=True)
class LldapSettings:
    """One guild's LLDAP connection, read from its ``moduleLldap`` config."""

    base_url: str
    ldap_url: str
    base_dn: str
    admin_username: str
    admin_password: str = field(repr=False)
    discord_attribute: str = DEFAULT_DISCORD_ATTRIBUTE

    @classmethod
    def from_section(cls, raw: Mapping[str, Any]) -> LldapSettings:
        def text(key: str) -> str:
            return str(raw.get(key) or "").strip()

        return cls(
            base_url=_normalize_url(text("lldapUrl"), "https"),
            ldap_url=_normalize_url(text("lldapLdapUrl"), "ldap"),
            base_dn=text("lldapBaseDn"),
            admin_username=text("lldapAdminUsername"),
            admin_password=text("lldapAdminPassword"),
            discord_attribute=text("lldapDiscordAttribute").lower() or DEFAULT_DISCORD_ATTRIBUTE,
        )

    @property
    def configured(self) -> bool:
        return all(
            (self.base_url, self.ldap_url, self.base_dn, self.admin_username, self.admin_password)
        )

    @property
    def instance_key(self) -> str:
        """Identifies the LLDAP server: guilds sharing it sync its groups together."""
        return self.base_url

    def user_dn(self, user_id: str) -> str:
        return f"uid={user_id},ou=people,{self.base_dn}"

    @property
    def bind_dn(self) -> str:
        return self.user_dn(self.admin_username)


@dataclass(frozen=True)
class LldapUser:
    """An LLDAP user as the role sync sees it."""

    user_id: str
    discord_id: str | None
    groups: frozenset[int]


def parse_users(data: Mapping[str, Any], discord_attribute: str) -> list[LldapUser]:
    """Turn a ``users { id attributes { name value } groups { id } }`` answer into users."""
    users: list[LldapUser] = []
    for raw in data.get("users") or []:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str):
            continue
        discord_id = None
        for attribute in raw.get("attributes") or []:
            if str(attribute.get("name", "")).lower() != discord_attribute:
                continue
            values = attribute.get("value") or []
            if values and str(values[0]).strip():
                discord_id = str(values[0]).strip()
            break
        groups = frozenset(
            int(group["id"])
            for group in raw.get("groups") or []
            if isinstance(group.get("id"), int)
        )
        users.append(LldapUser(user_id=raw["id"], discord_id=discord_id, groups=groups))
    return users


def _graphql_error(errors: Any) -> str:
    first = errors[0] if isinstance(errors, list) and errors else errors
    message = first.get("message") if isinstance(first, Mapping) else first
    return f"LLDAP a refusé la requête : {str(message)[:300]}"


_LIST_USERS = """
query ListUsers {
  users { id attributes { name value } groups { id } }
}
"""

_FIND_USERS = """
query FindUsers($filters: RequestFilter) {
  users(filters: $filters) { id }
}
"""

_CREATE_USER = """
mutation CreateUser($user: CreateUserInput!) {
  createUser(user: $user) { id }
}
"""

_DELETE_USER = """
mutation DeleteUser($userId: String!) {
  deleteUser(userId: $userId) { ok }
}
"""

_ADD_TO_GROUP = """
mutation AddUserToGroup($userId: String!, $groupId: Int!) {
  addUserToGroup(userId: $userId, groupId: $groupId) { ok }
}
"""

_REMOVE_FROM_GROUP = """
mutation RemoveUserFromGroup($userId: String!, $groupId: Int!) {
  removeUserFromGroup(userId: $userId, groupId: $groupId) { ok }
}
"""


def _ldap_set_password(settings: LldapSettings, user_id: str, password: str) -> None:
    """Blocking: bind as the admin and reset *user_id*'s password (RFC 3062)."""
    # Imported here: ldap3 is only needed once an account is created.
    import ldap3
    from ldap3.core.exceptions import LDAPException

    try:
        server = ldap3.Server(settings.ldap_url, connect_timeout=LDAP_TIMEOUT_SECONDS)
        conn = ldap3.Connection(
            server,
            user=settings.bind_dn,
            password=settings.admin_password,
            receive_timeout=LDAP_TIMEOUT_SECONDS,
        )
        try:
            if not conn.bind():
                raise LldapError(
                    f"LDAP a refusé le compte administrateur : {conn.result.get('description')}"
                )
            if not conn.extend.standard.modify_password(settings.user_dn(user_id), None, password):
                raise LldapError(
                    f"LDAP a refusé le mot de passe : {conn.result.get('description')}"
                )
        finally:
            conn.unbind()
    except LDAPException as e:
        raise LldapError(f"LDAP injoignable ({type(e).__name__})") from e


class LldapClient:
    """Talks to any number of LLDAP servers, one cached JWT per server."""

    def __init__(
        self,
        session_factory: Callable[[], Awaitable[ClientSession]] = http_client.session,
    ):
        self._session_factory = session_factory
        self._tokens: dict[tuple[str, str, str], str] = {}
        self._login_lock = asyncio.Lock()

    # ── Transport ────────────────────────────────────────────────────

    async def _post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        session = await self._session_factory()
        try:
            async with session.post(url, json=payload, headers=headers) as response:
                try:
                    body = await response.json(content_type=None)
                except ValueError:
                    body = None
                return response.status, body
        except (TimeoutError, ClientError) as e:
            raise LldapError(f"LLDAP injoignable ({type(e).__name__})") from e

    async def _token(self, settings: LldapSettings, stale: str | None = None) -> str:
        """The server's JWT, logging in when there is none or *stale* was refused."""
        key = (settings.base_url, settings.admin_username, settings.admin_password)
        async with self._login_lock:
            cached = self._tokens.get(key)
            if cached and cached != stale:
                return cached
            status, body = await self._post(
                f"{settings.base_url}/auth/simple/login",
                {"username": settings.admin_username, "password": settings.admin_password},
            )
            if status in (401, 403):
                raise LldapError("LLDAP a refusé les identifiants administrateur.")
            token = body.get("token") if isinstance(body, dict) else None
            if status != 200 or not isinstance(token, str):
                raise LldapError(f"Connexion à LLDAP impossible (HTTP {status}).")
            self._tokens[key] = token
            return token

    async def _graphql(
        self, settings: LldapSettings, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not settings.configured:
            raise LldapError("LLDAP n'est pas configuré sur ce serveur.")
        url = f"{settings.base_url}/api/graphql"
        payload = {"query": query, "variables": variables or {}}
        token = await self._token(settings)
        status, body = await self._post(url, payload, {"Authorization": f"Bearer {token}"})
        if status == 401:
            # The JWT expired (LLDAP issues them for a day): log in again once.
            token = await self._token(settings, stale=token)
            status, body = await self._post(url, payload, {"Authorization": f"Bearer {token}"})
        if isinstance(body, dict) and body.get("errors"):
            raise LldapError(_graphql_error(body["errors"]))
        data = body.get("data") if isinstance(body, dict) else None
        if status != 200 or not isinstance(data, dict):
            raise LldapError(f"LLDAP a répondu HTTP {status}.")
        return data

    async def _mutate_ok(
        self, settings: LldapSettings, mutation: str, name: str, variables: dict[str, Any]
    ) -> None:
        data = await self._graphql(settings, mutation, variables)
        result = data.get(name)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise LldapError(f"LLDAP n'a pas confirmé {name}.")

    # ── Users and groups ─────────────────────────────────────────────

    async def list_users(self, settings: LldapSettings) -> list[LldapUser]:
        """Every user with its linked Discord id and group ids, in one request."""
        data = await self._graphql(settings, _LIST_USERS)
        return parse_users(data, settings.discord_attribute)

    async def find_user_ids(self, settings: LldapSettings, field: str, value: str) -> list[str]:
        """Ids of the users whose *field* (``email``, a custom attribute…) equals *value*."""
        data = await self._graphql(
            settings, _FIND_USERS, {"filters": {"eq": {"field": field, "value": value}}}
        )
        return [u["id"] for u in data.get("users") or [] if isinstance(u.get("id"), str)]

    async def create_user(
        self,
        settings: LldapSettings,
        *,
        user_id: str,
        email: str,
        display_name: str,
        discord_id: str,
    ) -> None:
        await self._graphql(
            settings,
            _CREATE_USER,
            {
                "user": {
                    "id": user_id,
                    "email": email,
                    "displayName": display_name,
                    "attributes": [{"name": settings.discord_attribute, "value": [discord_id]}],
                }
            },
        )

    async def delete_user(self, settings: LldapSettings, user_id: str) -> None:
        await self._mutate_ok(settings, _DELETE_USER, "deleteUser", {"userId": user_id})

    async def add_to_group(self, settings: LldapSettings, user_id: str, group_id: int) -> None:
        await self._mutate_ok(
            settings, _ADD_TO_GROUP, "addUserToGroup", {"userId": user_id, "groupId": group_id}
        )

    async def remove_from_group(self, settings: LldapSettings, user_id: str, group_id: int) -> None:
        await self._mutate_ok(
            settings,
            _REMOVE_FROM_GROUP,
            "removeUserFromGroup",
            {"userId": user_id, "groupId": group_id},
        )

    async def set_password(self, settings: LldapSettings, user_id: str, password: str) -> None:
        """Set *user_id*'s password over LDAP (the GraphQL API has no such mutation)."""
        if not settings.configured:
            raise LldapError("LLDAP n'est pas configuré sur ce serveur.")
        await asyncio.to_thread(_ldap_set_password, settings, user_id, password)


__all__ = [
    "DEFAULT_DISCORD_ATTRIBUTE",
    "LldapClient",
    "LldapError",
    "LldapSettings",
    "LldapUser",
    "parse_users",
]
