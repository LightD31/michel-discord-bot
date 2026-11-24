"""Tests for the dashboard's Spotify statistics endpoint.

The router is mounted on a bare FastAPI app with a stub context and a fake
repository, so the tests exercise the auth hook, the wiring into
``features.spotify.aggregate`` and the error mapping without MongoDB.
"""

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from webui_fakes import FakeWebUI

import src.webui.routes.spotify as spotify_routes
from src.core.errors import DatabaseError
from src.webui.errors import install_error_handlers


@dataclass
class _Session:
    username: str = "admin"
    user_id: str = "42"


def _ctx() -> FakeWebUI:
    return FakeWebUI(session=_Session())


class _FakeRepo:
    error: Exception | None = None
    guild_ids: list[str] = []

    def __init__(self, guild_id):
        type(self).guild_ids.append(str(guild_id))

    async def find_playlist_items(self, query):
        if self.error:
            raise self.error
        return {
            "t1": {
                "_id": "t1",
                "added_by": "7",
                "added_at": "2026-01-10T10:00:00Z",
                "duration_ms": 200_000,
                "name": "Song",
                "artists": ["Artist"],
            }
        }

    async def find_vote_docs(self, query):
        return {
            "t0": {
                "_id": "t0",
                "name": "Old - Song",
                "date": "2026-01-02",
                "state": "supprimée",
                "votes": {"7": "supprimer", "8": "conserver"},
            },
            "t1": {"_id": "t1", "name": "Artist - Song", "date": "2026-01-11", "votes": {}},
        }

    async def get_vote_infos(self):
        return {"_id": "current", "track_id": "t1", "message_id": "1"}


@pytest.fixture
def client(monkeypatch):
    _FakeRepo.error = None
    _FakeRepo.guild_ids = []
    monkeypatch.setattr(spotify_routes, "SpotifyRepository", _FakeRepo)
    ctx = _ctx()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(spotify_routes.create_router(ctx))
    return TestClient(app), ctx


def test_stats_aggregates_the_guild_collections(client):
    http, ctx = client
    resp = http.get("/api/servers/123/spotify/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert ctx.checked == ["123"]
    assert _FakeRepo.guild_ids == ["123"]
    assert body["summary"]["track_count"] == 1
    assert body["top_contributors"] == [{"key": "7", "count": 1}]
    assert body["votes"]["removed"] == 1
    assert body["voters"][0]["user_id"] == "7"
    assert body["current_poll"]["track_id"] == "t1"


def test_stats_requires_guild_admin(client):
    http, ctx = client
    ctx.manages = set()
    resp = http.get("/api/servers/123/spotify/stats")
    assert resp.status_code == 403
    assert _FakeRepo.guild_ids == []


def test_stats_answer_an_outage_with_a_503(client):
    """It was a 500 here and a 503 everywhere else (P31)."""
    http, _ = client
    _FakeRepo.error = DatabaseError("boom")
    resp = http.get("/api/servers/123/spotify/stats")
    assert resp.status_code == 503
    assert "boom" not in resp.json()["detail"]
