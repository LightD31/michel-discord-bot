"""Tests for the Web UI auth hardening.

Covers: OAuth URL encoding, guarded session construction from Discord
payloads, MongoDB (de)serialization round-trip, per-event-loop aiohttp
sessions, OAuth state (CSRF) validation in the callback, and the
restore/purge persistence glue (with a stubbed repository — no MongoDB).
"""

import asyncio
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.requests import Request

import src.webui.auth as auth_module
from src.core.http import http_client
from src.webui.auth import (
    DiscordOAuth,
    Session,
    _doc_to_session,
    _session_to_doc,
)
from src.webui.context import WebUIContext
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
        "access_token": "at",
        "refresh_token": "rt",
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
    assert session.refresh_token == ""
    assert session.guilds == []
    assert session.expires_at > time.time()


# ── Mongo (de)serialization ─────────────────────────────────────────────


def test_session_doc_round_trip():
    session = make_session()
    doc = _session_to_doc(session)
    assert doc["_id"] == session.session_token
    assert "session_token" not in doc
    restored = _doc_to_session(doc)
    assert restored == session


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
        oauth.sessions[session.session_token] = session
        request = SimpleNamespace(cookies={"michel_session": session.session_token})
        return json.loads(asyncio.run(endpoint(request)).body)

    # Developer managing nothing: both bot guilds, names/icons from the cache
    dev = make_session(user_id="42", guilds=[])
    out = call(dev)
    assert set(out) == {"1", "2"}
    assert out["1"]["name"] == "Coloc" and out["1"]["icon"] == "abc123"
    assert out["2"]["name"] == "Autre serveur" and out["2"]["config"] == {}

    # Plain admin managing guild 1: only guild 1, OAuth name takes precedence
    admin = make_session(
        user_id="7", guilds=[{"id": "1", "name": "Coloc (oauth)", "permissions": "32"}]
    )
    out = call(admin)
    assert set(out) == {"1"}
    assert out["1"]["name"] == "Coloc (oauth)"


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


def test_user_manages_guild_checks_that_specific_guild():
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
    assert ctx.user_manages_guild(session, "1")
    assert ctx.user_manages_guild(session, 2)  # int ids accepted
    assert ctx.user_manages_guild(session, "3")
    assert not ctx.user_manages_guild(session, "4")
    assert not ctx.user_manages_guild(session, "999")  # not even a member


def test_developer_bypasses_guild_check():
    oauth = make_oauth(developer_user_ids=["42"])
    ctx = WebUIContext(bot=None, bot_loop=None, oauth=oauth)
    session = make_session(guilds=[])
    assert ctx.user_manages_guild(session, "999")
    assert ctx.is_admin_user(session)  # developer is admin even with no bot


def test_require_guild_admin_enforces_per_guild_access():
    from fastapi import HTTPException

    oauth = make_oauth()
    ctx = WebUIContext(bot=None, bot_loop=None, oauth=oauth)
    session = make_session(guilds=[{"id": "1", "permissions": "32"}])
    oauth.sessions[session.session_token] = session

    # No session cookie → 401
    with pytest.raises(HTTPException) as exc:
        ctx.require_guild_admin(make_authed_request(None), "1")
    assert exc.value.status_code == 401

    # Managing guild 1 grants nothing on guild 2 → 403
    with pytest.raises(HTTPException) as exc:
        ctx.require_guild_admin(make_authed_request(session.session_token), "2")
    assert exc.value.status_code == 403

    # The managed guild itself is allowed
    granted = ctx.require_guild_admin(make_authed_request(session.session_token), "1")
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
    assert oauth.get_session(fresh.session_token) is not None
    assert oauth.get_session(stale.session_token) is None


def test_purge_expired_evicts_memory(stub_repo):
    oauth = make_oauth()
    fresh = make_session(expires_in=3600)
    stale = make_session(expires_in=-60)
    oauth.sessions = {s.session_token: s for s in (fresh, stale)}

    purged = asyncio.run(oauth.purge_expired())
    assert purged == 1
    assert fresh.session_token in oauth.sessions
    assert stale.session_token not in oauth.sessions


def test_invalidate_session_removes_everywhere(stub_repo):
    oauth = make_oauth()
    session = make_session()
    oauth.sessions[session.session_token] = session

    asyncio.run(oauth.invalidate_session(session.session_token))
    assert session.session_token not in oauth.sessions
    assert stub_repo.deleted == [session.session_token]
