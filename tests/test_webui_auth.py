"""Tests for the Web UI auth hardening.

Covers: OAuth URL encoding, guarded session construction from Discord
payloads, MongoDB (de)serialization round-trip (hashed token, no OAuth
tokens), per-event-loop aiohttp sessions, OAuth state (CSRF) validation in
the callback, live per-guild authorization, and the restore/purge
persistence glue (with a stubbed repository — no MongoDB).
"""

import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.requests import Request

import src.webui.auth as auth_module
from src.core.http import http_client
from src.webui.auth import (
    SESSION_TTL_SECONDS,
    DiscordOAuth,
    Session,
    _doc_to_session,
    _session_to_doc,
    hash_token,
)
from src.webui.context import WebUIContext
from src.webui.guild_access import NO_ACCESS, GuildAccess, read_guild_access
from src.webui.routes import auth as auth_routes


def make_oauth(**kwargs) -> DiscordOAuth:
    defaults = {
        "client_id": "123",
        "client_secret": "s3cret",  # pragma: allowlist secret
        "redirect_uri": "https://michel.example/auth/callback",
    }
    defaults.update(kwargs)
    return DiscordOAuth(**defaults)


def make_session(expires_in: float = 3600.0, **kwargs) -> Session:
    defaults = {
        "user_id": "42",
        "username": "michel",
        "avatar": None,
        "guilds": [{"id": "1", "permissions": "32"}],
        "expires_at": time.time() + expires_in,
    }
    defaults.update(kwargs)
    return Session(**defaults)


# ── OAuth URL ───────────────────────────────────────────────────────────


def test_oauth_url_is_properly_encoded():
    url = make_oauth().get_oauth_url(state="a/b c")
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert query["redirect_uri"] == ["https://michel.example/auth/callback"]
    assert query["state"] == ["a/b c"]
    assert "redirect_uri=https://" not in url  # the raw URI must be escaped


# ── Payload guards ──────────────────────────────────────────────────────


def test_session_from_payload_requires_token_and_id():
    build = DiscordOAuth._session_from_payload
    assert build({}, {"id": "1"}, []) is None
    assert build({"access_token": "at"}, {}, []) is None


def test_session_from_payload_tolerates_partial_data():
    session = DiscordOAuth._session_from_payload(
        {"access_token": "at", "expires_in": "oops"},
        {"id": 42},
        "not-a-list",
    )
    assert session is not None
    assert session.user_id == "42"
    assert session.username == "42"  # falls back to the id
    assert session.guilds == []
    assert session.expires_at > time.time()


def test_session_lifetime_ignores_discord_token_expiry():
    """The session lasts as long as its cookie, not Discord's ~7-day token."""
    before = time.time()
    session = DiscordOAuth._session_from_payload(
        {"access_token": "at", "refresh_token": "rt", "expires_in": 604800},
        {"id": "1"},
        [],
    )
    assert session is not None
    assert before + SESSION_TTL_SECONDS <= session.expires_at <= time.time() + SESSION_TTL_SECONDS
    assert not hasattr(session, "access_token")
    assert not hasattr(session, "refresh_token")


def test_session_cookie_max_age_matches_server_lifetime():
    resp = call_callback(state_cookie="expected", state="expected")
    set_cookie = ",".join(resp.headers.getlist("set-cookie"))
    assert f"Max-Age={SESSION_TTL_SECONDS}" in set_cookie


# ── Mongo (de)serialization ─────────────────────────────────────────────


def test_session_doc_round_trip():
    session = make_session()
    doc = _session_to_doc(session)
    assert doc["_id"] == hash_token(session.session_token) == session.token_hash
    restored = _doc_to_session(doc)
    assert restored == session
    assert restored.session_token == ""  # the cookie value is never persisted


def test_session_doc_holds_nothing_replayable():
    """A leaked document must not contain the cookie value or OAuth tokens."""
    session = make_session()
    doc = _session_to_doc(session)
    assert "session_token" not in doc
    assert session.session_token not in repr(doc)
    assert not {"access_token", "refresh_token"} & set(doc)


# ── Per-loop aiohttp sessions ───────────────────────────────────────────


def test_http_client_one_session_per_loop():
    async def grab():
        first = await http_client.session()
        second = await http_client.session()
        assert second is first
        await http_client.close()
        return first

    a = asyncio.run(grab())
    b = asyncio.run(grab())  # fresh loop → fresh session
    assert a is not b


# ── /api/servers listing ────────────────────────────────────────────────


def test_server_list_scoping_and_bot_cache_names():
    """Developers see every bot guild with cache-resolved names; admins only theirs."""
    import json
    from types import SimpleNamespace

    import src.webui.routes.servers as servers_routes

    oauth = make_oauth(developer_user_ids=["42"])
    bot = SimpleNamespace(
        guilds=[
            SimpleNamespace(id=1, name="Coloc", icon=SimpleNamespace(hash="abc123")),
            SimpleNamespace(id=2, name="Autre serveur", icon=None),
        ]
    )
    ctx = WebUIContext(bot=bot, bot_loop=None, oauth=oauth)
    # Guild 99 is configured but the bot left it — must never be listed.
    ctx.get_full_config = lambda: {"servers": {"1": {"moduleXp": {}}, "99": {}}}
    router = servers_routes.create_router(ctx)
    endpoint = next(r for r in router.routes if r.path == "/api/servers").endpoint

    def call(session):
        oauth.sessions[session.token_hash] = session
        request = SimpleNamespace(cookies={"michel_session": session.session_token})
        return json.loads(asyncio.run(endpoint(request)).body)

    # Developer managing nothing: both bot guilds, names/icons from the cache
    dev = make_session(user_id="42", guilds=[])
    out = call(dev)
    assert set(out) == {"1", "2"}
    assert out["1"]["name"] == "Coloc" and out["1"]["icon"] == "abc123"
    assert out["2"]["name"] == "Autre serveur" and out["2"]["config"] == {}

    # Plain admin: who manages what is read live, not from the OAuth snapshot.
    live = {("1", "7"): GuildAccess(is_member=True, permissions=0x20)}

    async def fake_access(session, guild_id):
        return live.get((str(guild_id), session.user_id), NO_ACCESS)

    ctx.guild_access = fake_access
    admin = make_session(
        user_id="7",
        guilds=[
            {"id": "1", "name": "Coloc (oauth)", "permissions": "32"},
            {"id": "2", "name": "Autre", "permissions": "0"},
        ],
    )
    out = call(admin)
    assert set(out) == {"1"}
    assert out["1"]["name"] == "Coloc (oauth)"

    # Demoted since login: the snapshot still says MANAGE_GUILD, live says no.
    live.clear()
    assert call(admin) == {}

    # Promoted since login on guild 2: listed although the snapshot says plain member.
    live[("2", "7")] = GuildAccess(is_member=True, permissions=0x8)
    assert set(call(admin)) == {"2"}


# ── Callback state (CSRF) validation ────────────────────────────────────
#
# The route endpoint is invoked directly with a hand-built starlette Request
# (no TestClient — it drags in an HTTP client dependency for no benefit here).


def get_callback_endpoint():
    oauth = make_oauth()

    async def fake_exchange(code: str):
        return make_session() if code == "good-code" else None

    oauth.exchange_code = fake_exchange
    ctx = WebUIContext(bot=None, bot_loop=None, oauth=oauth)
    router = auth_routes.create_router(ctx)
    route = next(r for r in router.routes if r.path == "/auth/callback")
    return route.endpoint


def make_request(state_cookie: str | None) -> Request:
    headers = []
    if state_cookie is not None:
        headers.append((b"cookie", f"oauth_state={state_cookie}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/auth/callback",
            "query_string": b"",
            "headers": headers,
        }
    )


def call_callback(state_cookie: str | None, state: str):
    endpoint = get_callback_endpoint()
    return asyncio.run(endpoint(make_request(state_cookie), code="good-code", state=state))


def test_callback_rejects_mismatched_state():
    resp = call_callback(state_cookie="expected", state="tampered")
    assert resp.status_code == 403


def test_callback_rejects_missing_state_cookie():
    resp = call_callback(state_cookie=None, state="whatever")
    assert resp.status_code == 403


def test_callback_accepts_matching_state():
    resp = call_callback(state_cookie="expected", state="expected")
    assert resp.status_code == 307  # redirect home with the session cookie
    set_cookie = ",".join(resp.headers.getlist("set-cookie"))
    assert "michel_session=" in set_cookie


# ── Per-guild authorization ─────────────────────────────────────────────


def make_authed_request(session_token: str | None) -> Request:
    headers = []
    if session_token is not None:
        headers.append((b"cookie", f"michel_session={session_token}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/api/servers/1",
            "query_string": b"",
            "headers": headers,
        }
    )


def test_without_bot_falls_back_to_oauth_snapshot():
    """Standalone dashboard (no bot client): only the login-time snapshot exists."""
    oauth = make_oauth()
    ctx = WebUIContext(bot=None, bot_loop=None, oauth=oauth)
    session = make_session(
        guilds=[
            {"id": "1", "permissions": "32"},  # MANAGE_GUILD
            {"id": "2", "permissions": "8"},  # ADMINISTRATOR
            {"id": "3", "owner": True, "permissions": "0"},
            {"id": "4", "permissions": "0"},  # plain member
        ]
    )
    manages = lambda gid: asyncio.run(ctx.user_manages_guild(session, gid))  # noqa: E731
    assert manages("1")
    assert manages(2)  # int ids accepted
    assert manages("3")
    assert not manages("4")
    assert not manages("999")  # not even a member


def test_developer_bypasses_guild_check():
    oauth = make_oauth(developer_user_ids=["42"])
    ctx = WebUIContext(bot=None, bot_loop=None, oauth=oauth)
    session = make_session(guilds=[])
    assert asyncio.run(ctx.user_manages_guild(session, "999"))
    assert ctx.is_admin_user(session)  # developer is admin even with no bot


# Live checks run on the "bot loop"; here that is the test's own loop, which
# run_coroutine_threadsafe accepts as long as it keeps running.


class FakeMember:
    def __init__(self, user_id: int, permissions: int) -> None:
        self.id = user_id
        self.guild_permissions = permissions


class FakeGuild:
    def __init__(self, guild_id: int, owner_id: int, members: dict[int, FakeMember]) -> None:
        self.id = guild_id
        self.owner_id = owner_id
        self.members = members
        self.fetches = 0

    async def fetch_member(self, user_id: int):
        self.fetches += 1
        return self.members.get(int(user_id))

    def is_owner(self, user_id) -> bool:
        return int(user_id) == self.owner_id


class FakeBot:
    def __init__(self, *guilds: FakeGuild) -> None:
        self.guilds = list(guilds)
        self.is_ready = True

    def get_guild(self, guild_id: int):
        return next((g for g in self.guilds if g.id == int(guild_id)), None)


def live_ctx(bot) -> WebUIContext:
    return WebUIContext(bot=bot, bot_loop=None, oauth=make_oauth())


def run_live(ctx: WebUIContext, coro_factory):
    async def main():
        ctx.bot_loop = asyncio.get_running_loop()
        return await coro_factory()

    return asyncio.run(main())


def test_read_guild_access_resolves_membership_and_permissions():
    guild = FakeGuild(1, owner_id=5, members={7: FakeMember(7, 0x20), 5: FakeMember(5, 0)})
    bot = FakeBot(guild)
    read = lambda gid, uid: asyncio.run(read_guild_access(bot, gid, uid))  # noqa: E731
    assert read("1", "7") == GuildAccess(is_member=True, permissions=0x20)
    assert read("1", "5").manages_guild  # owner, whatever the permission bits
    assert read("1", "8") is NO_ACCESS  # not a member
    assert read("2", "7") is NO_ACCESS  # bot not in that guild


def test_live_check_overrides_a_stale_oauth_snapshot():
    """Demoted after login: the snapshot still claims MANAGE_GUILD, the bot knows better."""
    from fastapi import HTTPException

    guild = FakeGuild(1, owner_id=5, members={42: FakeMember(42, 0)})
    ctx = live_ctx(FakeBot(guild))
    session = make_session(guilds=[{"id": "1", "permissions": "32"}])
    ctx.oauth.sessions[session.token_hash] = session
    request = make_authed_request(session.session_token)

    with pytest.raises(HTTPException) as exc:
        run_live(ctx, lambda: ctx.require_guild_admin(request, "1"))
    assert exc.value.status_code == 403

    # Promoted: allowed even though the snapshot says nothing.
    guild.members[42] = FakeMember(42, 0x8)
    ctx._access_cache.clear()
    assert run_live(ctx, lambda: ctx.require_guild_admin(request, "1")) is session


def test_live_check_is_cached_briefly():
    guild = FakeGuild(1, owner_id=5, members={42: FakeMember(42, 0x20)})
    ctx = live_ctx(FakeBot(guild))
    session = make_session()

    async def twice():
        first = await ctx.user_manages_guild(session, "1")
        second = await ctx.user_manages_guild(session, "1")
        return first, second

    assert run_live(ctx, twice) == (True, True)
    assert guild.fetches == 1


def test_live_check_fails_closed():
    """If the bot can't answer, access is refused (503), never granted."""
    from fastapi import HTTPException

    class BrokenBot(FakeBot):
        def get_guild(self, guild_id):
            raise RuntimeError("gateway down")

    ctx = live_ctx(BrokenBot())
    session = make_session(guilds=[{"id": "1", "permissions": "8"}])
    with pytest.raises(HTTPException) as exc:
        run_live(ctx, lambda: ctx.user_manages_guild(session, "1"))
    assert exc.value.status_code == 503

    # Bot client present but its loop is gone: 503 as well.
    ctx = live_ctx(FakeBot())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ctx.user_manages_guild(session, "1"))
    assert exc.value.status_code == 503


def test_require_guild_admin_enforces_per_guild_access():
    from fastapi import HTTPException

    guild_one = FakeGuild(1, owner_id=5, members={42: FakeMember(42, 0x20)})
    guild_two = FakeGuild(2, owner_id=5, members={42: FakeMember(42, 0)})
    ctx = live_ctx(FakeBot(guild_one, guild_two))
    session = make_session()
    ctx.oauth.sessions[session.token_hash] = session

    # No session cookie → 401
    with pytest.raises(HTTPException) as exc:
        run_live(ctx, lambda: ctx.require_guild_admin(make_authed_request(None), "1"))
    assert exc.value.status_code == 401

    # Managing guild 1 grants nothing on guild 2 → 403
    with pytest.raises(HTTPException) as exc:
        run_live(
            ctx, lambda: ctx.require_guild_admin(make_authed_request(session.session_token), "2")
        )
    assert exc.value.status_code == 403

    # The managed guild itself is allowed
    granted = run_live(
        ctx, lambda: ctx.require_guild_admin(make_authed_request(session.session_token), "1")
    )
    assert granted is session


# ── Persistence glue (stubbed repository) ───────────────────────────────


class StubRepo:
    docs: list[dict] = []
    deleted: list[str] = []

    async def load_all_docs(self):
        return list(self.docs)

    async def upsert_doc(self, doc):
        self.docs.append(doc)

    async def delete(self, token):
        self.deleted.append(token)

    async def delete_expired(self, now_ts):
        return 0


@pytest.fixture
def stub_repo(monkeypatch):
    StubRepo.docs = []
    StubRepo.deleted = []
    monkeypatch.setattr(auth_module, "SessionRepository", StubRepo)
    return StubRepo


def test_restore_skips_expired_sessions(stub_repo):
    fresh = make_session(expires_in=3600)
    stale = make_session(expires_in=-60)
    stub_repo.docs = [_session_to_doc(fresh), _session_to_doc(stale)]

    oauth = make_oauth()
    restored = asyncio.run(oauth.restore_sessions())
    assert restored == 1
    # The browser still holds the raw cookie; it resolves through its hash.
    assert oauth.get_session(fresh.session_token) is not None
    assert oauth.get_session(stale.session_token) is None


def test_purge_expired_evicts_memory(stub_repo):
    oauth = make_oauth()
    fresh = make_session(expires_in=3600)
    stale = make_session(expires_in=-60)
    oauth.sessions = {s.token_hash: s for s in (fresh, stale)}

    purged = asyncio.run(oauth.purge_expired())
    assert purged == 1
    assert fresh.token_hash in oauth.sessions
    assert stale.token_hash not in oauth.sessions


def test_invalidate_session_removes_everywhere(stub_repo):
    oauth = make_oauth()
    session = make_session()
    oauth.sessions[session.token_hash] = session

    asyncio.run(oauth.invalidate_session(session.session_token))
    assert session.token_hash not in oauth.sessions
    assert stub_repo.deleted == [session.token_hash]  # never the raw cookie
