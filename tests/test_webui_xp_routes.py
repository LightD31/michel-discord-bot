"""Tests for the dashboard's XP statistics endpoint.

The router is mounted on a bare FastAPI app with a stub context and fake
repositories, so the tests exercise validation, shaping and error mapping
without an OAuth session or MongoDB.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytz
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.webui.routes.xp as xp_routes
from src.core.errors import DatabaseError


@dataclass
class _Session:
    username: str = "admin"
    user_id: str = "42"


class _Ctx:
    """Minimal stand-in for WebUIContext — the router only needs the auth hook."""

    def __init__(self):
        self.allowed = True
        self.checked: list[str] = []

    def require_guild_admin(self, request, guild_id):  # noqa: ARG002 — signature parity
        self.checked.append(str(guild_id))
        if not self.allowed:
            raise HTTPException(status_code=403, detail="Accès refusé")
        return _Session()


class _FakeXpRepo:
    error: Exception | None = None
    since: datetime | None = None

    def __init__(self, guild_id: str):
        self.guild_id = guild_id

    def _check(self):
        if _FakeXpRepo.error:
            raise _FakeXpRepo.error

    async def list_all_sorted_by_xp(self):
        self._check()
        return [
            {"_id": "1", "xp": 475, "msg": 20},
            {"_id": "2", "xp": 120, "msg": 5},
            {"_id": "3", "xp": 0, "msg": 1},
        ]

    async def daily_activity(self, since, tz):
        self._check()
        _FakeXpRepo.since = since
        day = datetime.now(UTC).astimezone(pytz.timezone(tz)).date().isoformat()
        return [
            {"day": day, "source": "message", "xp": 60, "events": 3},
            {"day": day, "source": "voice", "xp": 16, "events": 2},
        ]

    async def daily_active_users(self, since, tz):
        self._check()
        return []

    async def count_active_users(self, since):
        self._check()
        return 2

    async def activity_heatmap(self, since, tz):
        self._check()
        return [{"dow": 2, "hour": 21, "xp": 76}]


class _FakeUserInfoRepo:
    def __init__(self, guild_id: str):
        self.guild_id = guild_id

    async def get_names(self, user_ids):
        return {"1": {"username": "alice", "display_name": "Alice"}}


@pytest.fixture
def client(monkeypatch):
    _FakeXpRepo.error = None
    _FakeXpRepo.since = None
    monkeypatch.setattr(xp_routes, "XpRepository", _FakeXpRepo)
    monkeypatch.setattr(xp_routes, "UserInfoRepository", _FakeUserInfoRepo)
    ctx = _Ctx()
    app = FastAPI()
    app.include_router(xp_routes.create_router(ctx))
    return TestClient(app), ctx


def test_stats_payload_shape(client):
    http, ctx = client
    resp = http.get("/api/servers/123/xp/stats?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert ctx.checked == ["123"]

    assert body["days"] == 7
    assert body["timezone"] == "Europe/Paris"
    assert len(body["daily"]) == 7
    assert body["summary"]["members"] == 3
    assert body["summary"]["total_xp"] == 595
    assert body["summary"]["total_messages"] == 26
    assert body["summary"]["active_members"] == 2
    assert body["summary"]["period_voice_ticks"] == 2
    assert body["summary"]["period_voice_minutes"] == 6

    assert body["levels"] == [
        {"level": 0, "users": 1},
        {"level": 1, "users": 1},
        {"level": 2, "users": 0},
        {"level": 3, "users": 1},
    ]
    assert [m["display_name"] for m in body["top"]] == ["Alice", "2", "3"]
    assert body["top"][0]["level"] == 3
    assert len(body["heatmap"]) == 7
    assert body["heatmap"][0][21] == 76


def test_default_range_is_30_days(client):
    http, _ = client
    body = http.get("/api/servers/123/xp/stats").json()
    assert body["days"] == 30
    assert len(body["daily"]) == 30
    assert _FakeXpRepo.since is not None
    assert _FakeXpRepo.since.tzinfo is not None


def test_rejects_unsupported_range(client):
    http, _ = client
    resp = http.get("/api/servers/123/xp/stats?days=12")
    assert resp.status_code == 400
    assert "7, 30, 90, 365" in resp.json()["detail"]


def test_requires_guild_admin(client):
    http, ctx = client
    ctx.allowed = False
    assert http.get("/api/servers/123/xp/stats").status_code == 403


def test_database_error_is_surfaced_as_500(client):
    http, _ = client
    _FakeXpRepo.error = DatabaseError("MongoDB injoignable")
    resp = http.get("/api/servers/123/xp/stats")
    assert resp.status_code == 500
    assert "MongoDB" in resp.json()["detail"]
