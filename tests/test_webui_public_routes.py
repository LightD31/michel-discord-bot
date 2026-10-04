"""Tests for the unauthenticated public XP / Spotify dashboard endpoints.

The router is mounted on a bare FastAPI app with a stub context, a fake config
snapshot and fake repositories: no OAuth session, no MongoDB. What matters here
is the opt-in gate, the absence of Discord ids in the payloads, and the cache.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytz
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.webui.routes.public as public_routes
import src.webui.routes.spotify as spotify_routes
import src.webui.routes.xp as xp_routes
from features.spotify.stats import CurrentPoll, NamedCount, PlaylistStats, VoterTally
from src.core.errors import DatabaseError
from src.webui.public_links import (
    enabled_public_dashboards,
    public_dashboard_enabled,
    public_dashboard_url,
)

PUBLISHED = "111"
PRIVATE = "222"


def _config():
    return {
        "config": {},
        "servers": {
            PUBLISHED: {
                "serverName": "Le Serveur",
                "discord2name": {"7": "Septime"},
                "moduleXp": {"enabled": True, "publicDashboard": True},
                "moduleSpotify": {"enabled": True, "publicDashboard": True},
            },
            PRIVATE: {
                "moduleXp": {"enabled": True},
                # Flag set but module off: still private.
                "moduleSpotify": {"enabled": False, "publicDashboard": True},
            },
        },
    }


class _FakeXpRepo:
    calls = 0
    error: Exception | None = None

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def list_all_sorted_by_xp(self):
        type(self).calls += 1
        if self.error:
            raise self.error
        return [{"_id": "1", "xp": 475, "msg": 20}, {"_id": "2", "xp": 120, "msg": 5}]

    async def daily_activity(self, since, tz):
        day = datetime.now(UTC).astimezone(pytz.timezone(tz)).date().isoformat()
        return [{"day": day, "source": "message", "xp": 60, "events": 3}]

    async def daily_active_users(self, since, tz):
        return []

    async def count_active_users(self, since):
        return 2

    async def activity_heatmap(self, since, tz):
        return []


class _FakeUserInfoRepo:
    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def get_names(self, user_ids):
        known = {
            "1": {"username": "alice", "display_name": "Alice"},
            "7": {"username": "sept", "display_name": "Seven"},
            "8": {"username": "huit", "display_name": ""},
        }
        return {uid: known[uid] for uid in user_ids if uid in known}


class _FakeSpotifyRepo:
    calls = 0

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def find_playlist_items(self, query):
        type(self).calls += 1
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
                "added_by": "9",
                "state": "supprimée",
                "votes": {"7": "supprimer", "8": "conserver", "9": "menfou"},
            },
            "t1": {
                "_id": "t1",
                "name": "Artist - Song",
                "date": "2026-01-11",
                "added_by": "7",
                "votes": {"8": "conserver"},
            },
        }

    async def get_vote_infos(self):
        return {"_id": "current", "track_id": "t1", "message_id": "1"}


@pytest.fixture
def http(monkeypatch):
    _FakeXpRepo.calls = 0
    _FakeXpRepo.error = None
    _FakeSpotifyRepo.calls = 0
    cfg = _config()
    monkeypatch.setattr(public_routes, "config_store", SimpleNamespace(get=lambda: cfg))
    monkeypatch.setattr(xp_routes, "XpRepository", _FakeXpRepo)
    monkeypatch.setattr(xp_routes, "UserInfoRepository", _FakeUserInfoRepo)
    monkeypatch.setattr(spotify_routes, "SpotifyRepository", _FakeSpotifyRepo)
    monkeypatch.setattr(public_routes, "UserInfoRepository", _FakeUserInfoRepo)
    guild = SimpleNamespace(id=int(PUBLISHED), name="Vrai Nom", icon=SimpleNamespace(hash="abc"))
    ctx = SimpleNamespace(bot=SimpleNamespace(guilds=[guild]))
    app = FastAPI()
    app.include_router(public_routes.create_router(ctx))
    return TestClient(app)


# --- Opt-in gate ------------------------------------------------------------


def test_guild_meta_lists_published_dashboards(http):
    body = http.get(f"/api/public/{PUBLISHED}").json()
    assert body == {
        "id": PUBLISHED,
        "name": "Vrai Nom",
        "icon": "abc",
        "dashboards": ["xp", "spotify"],
    }


@pytest.mark.parametrize("server_id", [PRIVATE, "333", "abc", "111a"])
def test_unpublished_or_unknown_guilds_are_indistinguishable(http, server_id):
    for path in ("", "/xp/stats", "/spotify/stats"):
        resp = http.get(f"/api/public/{server_id}{path}")
        assert resp.status_code == 404, path
        assert resp.json()["detail"] == public_routes.NOT_FOUND
    assert _FakeXpRepo.calls == 0
    assert _FakeSpotifyRepo.calls == 0


def test_published_flag_requires_enabled_module():
    cfg = _config()["servers"]
    assert public_dashboard_enabled(cfg[PUBLISHED], "xp")
    assert not public_dashboard_enabled(cfg[PRIVATE], "xp")
    assert not public_dashboard_enabled(cfg[PRIVATE], "spotify")
    assert not public_dashboard_enabled(cfg[PUBLISHED], "unknown")
    assert enabled_public_dashboards(cfg[PRIVATE]) == []


def test_public_url_needs_an_enabled_webui_with_base_url():
    assert public_dashboard_url({}, 1, "xp") == ""
    assert public_dashboard_url({"webui": {"enabled": True}}, 1, "xp") == ""
    assert public_dashboard_url({"webui": {"baseUrl": "https://x.fr"}}, 1, "xp") == ""
    cfg = {"webui": {"enabled": True, "baseUrl": "https://x.fr/ "}}
    assert public_dashboard_url(cfg, 1, "spotify") == "https://x.fr/public/1/spotify"


# --- XP ---------------------------------------------------------------------


def test_xp_stats_drop_ids_and_handles(http):
    resp = http.get(f"/api/public/{PUBLISHED}/xp/stats?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    assert body["summary"]["members"] == 2
    assert [row["display_name"] for row in body["top"]] == ["Alice", "Membre inconnu"]
    assert all("user_id" not in row and "username" not in row for row in body["top"])


def test_xp_stats_validate_range(http):
    assert http.get(f"/api/public/{PUBLISHED}/xp/stats?days=12").status_code == 400


def test_xp_stats_are_cached_per_window(http):
    http.get(f"/api/public/{PUBLISHED}/xp/stats?days=7")
    http.get(f"/api/public/{PUBLISHED}/xp/stats?days=7")
    assert _FakeXpRepo.calls == 1
    http.get(f"/api/public/{PUBLISHED}/xp/stats?days=30")
    assert _FakeXpRepo.calls == 2


def test_xp_database_error_is_503_and_not_cached(http):
    _FakeXpRepo.error = DatabaseError("MongoDB injoignable")
    resp = http.get(f"/api/public/{PUBLISHED}/xp/stats")
    assert resp.status_code == 503
    assert "MongoDB" not in resp.json()["detail"]
    _FakeXpRepo.error = None
    assert http.get(f"/api/public/{PUBLISHED}/xp/stats").status_code == 200


# --- Spotify ----------------------------------------------------------------


def test_spotify_stats_replace_user_ids_with_names(http):
    resp = http.get(f"/api/public/{PUBLISHED}/spotify/stats")
    assert resp.status_code == 200
    body = resp.json()
    # Configured first name wins over the Discord display name.
    assert body["top_contributors"] == [{"key": "Septime", "count": 1}]
    assert {v["user_id"] for v in body["voters"]} == {"Septime", "huit", "Membre inconnu"}
    assert body["current_poll"]["added_by"] == "Septime"
    assert body["current_poll"]["voters"] == ["huit"]
    assert body["top_artists"] == [{"key": "Artist", "count": 1}]
    raw = resp.text
    assert '"7"' not in raw and '"8"' not in raw and '"9"' not in raw


def test_spotify_stats_are_cached(http):
    http.get(f"/api/public/{PUBLISHED}/spotify/stats")
    http.get(f"/api/public/{PUBLISHED}/spotify/stats")
    assert _FakeSpotifyRepo.calls == 1


def test_playlist_stats_relabel_users_only():
    stats = PlaylistStats(
        top_contributors=[NamedCount("1", 3)],
        top_artists=[NamedCount("1", 2)],
        voters=[VoterTally("2", 1, 0, 0)],
        current_poll=CurrentPoll("t", "n", None, None, 0, 0, 0, voters=["3"]),
    )
    assert stats.user_ids() == {"1", "2", "3"}
    labelled = stats.with_user_labels(lambda uid: f"user{uid}")
    assert labelled.top_contributors[0].key == "user1"
    assert labelled.top_artists[0].key == "1"
    assert labelled.voters[0].user_id == "user2"
    assert labelled.current_poll is not None
    assert labelled.current_poll.voters == ["user3"]
    assert labelled.current_poll.added_by is None
    # The original is untouched.
    assert stats.top_contributors[0].key == "1"
