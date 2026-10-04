"""Tests for the unauthenticated public XP / Spotify dashboard endpoints.

The router is mounted on a bare FastAPI app with a stub context, a fake config
snapshot and fake repositories: no OAuth session, no MongoDB. What matters here
is the secret-token gate, the opt-in, the absence of Discord ids and of open
poll results in the payloads, and the cache.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytz
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.webui.public_links as public_links
import src.webui.routes.public as public_routes
import src.webui.routes.spotify as spotify_routes
import src.webui.routes.xp as xp_routes
from features.spotify.stats import CurrentPoll, NamedCount, PlaylistStats, VoterTally
from src.core.errors import DatabaseError
from src.webui.public_links import (
    TOKEN_KEY,
    enabled_public_dashboards,
    ensure_public_token,
    find_guild_by_token,
    public_dashboard_enabled,
    public_dashboard_url,
)

PUBLISHED = "111"
PRIVATE = "222"
TOKEN = "pubtoken-0123456789abcdef"  # pragma: allowlist secret — test fixture
PRIVATE_TOKEN = "privtoken-0123456789abcdef"  # pragma: allowlist secret — test fixture


def _config():
    return {
        "config": {},
        "servers": {
            PUBLISHED: {
                "serverName": "Le Serveur",
                TOKEN_KEY: TOKEN,
                "discord2name": {"7": "Septime"},
                "moduleXp": {"enabled": True, "publicDashboard": True},
                "moduleSpotify": {"enabled": True, "publicDashboard": True},
            },
            PRIVATE: {
                TOKEN_KEY: PRIVATE_TOKEN,
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


class _FakeStore:
    """In-memory stand-in for ``config_store`` (get + atomic mutate)."""

    def __init__(self, data):
        self.data = data
        self.writes = 0

    def get(self):
        return self.data

    def mutate(self, mutator):
        mutator(self.data)
        self.writes += 1
        return self.data


class _Ctx:
    def __init__(self, store):
        guild = SimpleNamespace(
            id=int(PUBLISHED), name="Vrai Nom", icon=SimpleNamespace(hash="abc")
        )
        self.bot = SimpleNamespace(guilds=[guild])
        self.allowed = True
        self.mutate_config = store.mutate

    def require_guild_admin(self, request, guild_id):  # noqa: ARG002 — signature parity
        if not self.allowed:
            raise HTTPException(status_code=403, detail="nope")
        return SimpleNamespace(username="admin", user_id="42")


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore(_config())
    monkeypatch.setattr(public_routes, "config_store", fake)
    monkeypatch.setattr(public_links, "config_store", fake)
    return fake


@pytest.fixture
def ctx(store):
    return _Ctx(store)


@pytest.fixture
def http(monkeypatch, ctx):
    _FakeXpRepo.calls = 0
    _FakeXpRepo.error = None
    _FakeSpotifyRepo.calls = 0
    monkeypatch.setattr(xp_routes, "XpRepository", _FakeXpRepo)
    monkeypatch.setattr(xp_routes, "UserInfoRepository", _FakeUserInfoRepo)
    monkeypatch.setattr(spotify_routes, "SpotifyRepository", _FakeSpotifyRepo)
    monkeypatch.setattr(public_routes, "UserInfoRepository", _FakeUserInfoRepo)
    app = FastAPI()
    app.include_router(public_routes.create_router(ctx))
    return TestClient(app)


# --- Token + opt-in gate ----------------------------------------------------


def test_guild_meta_lists_published_dashboards(http):
    body = http.get(f"/api/public/{TOKEN}").json()
    assert body == {
        "id": PUBLISHED,
        "name": "Vrai Nom",
        "icon": "abc",
        "dashboards": ["xp", "spotify"],
    }


@pytest.mark.parametrize(
    "key",
    [
        PUBLISHED,  # the guild id no longer opens anything
        PRIVATE_TOKEN,  # valid token, nothing published
        TOKEN[:-1],  # near miss
        TOKEN + "x",
        "short",
        "é" * 20,
    ],
)
def test_ids_wrong_or_unpublished_tokens_are_indistinguishable(http, key):
    for path in ("", "/xp/stats", "/spotify/stats"):
        resp = http.get(f"/api/public/{key}{path}")
        assert resp.status_code == 404, path
        assert resp.json()["detail"] == public_routes.NOT_FOUND
    assert _FakeXpRepo.calls == 0
    assert _FakeSpotifyRepo.calls == 0


def test_find_guild_by_token_skips_guilds_without_token():
    servers = {"1": {"moduleXp": {}}, "2": {TOKEN_KEY: TOKEN}, "3": "not-a-dict"}
    assert find_guild_by_token(servers, TOKEN) == ("2", {TOKEN_KEY: TOKEN})
    assert find_guild_by_token(servers, "") is None


# --- Admin link endpoints ---------------------------------------------------


def test_link_endpoint_mints_once_then_reuses(http, store):
    store.data["servers"]["333"] = {"moduleXp": {"enabled": True, "publicDashboard": True}}
    first = http.get("/api/servers/333/public-dashboard").json()
    assert len(first["token"]) >= 20
    assert first["dashboards"] == ["xp"]
    assert store.writes == 1
    second = http.get("/api/servers/333/public-dashboard").json()
    assert second["token"] == first["token"]
    assert store.writes == 1  # an existing token is never rewritten
    assert http.get(f"/api/public/{first['token']}").status_code == 200


def test_rotate_revokes_the_old_link(http, store):
    new = http.post(f"/api/servers/{PUBLISHED}/public-dashboard/rotate").json()["token"]
    assert new != TOKEN
    assert http.get(f"/api/public/{TOKEN}").status_code == 404
    assert http.get(f"/api/public/{new}/xp/stats").status_code == 200


def test_link_endpoints_require_guild_admin(http, ctx, store):
    ctx.allowed = False
    assert http.get(f"/api/servers/{PUBLISHED}/public-dashboard").status_code == 403
    assert http.post(f"/api/servers/{PUBLISHED}/public-dashboard/rotate").status_code == 403
    assert store.writes == 0


def test_ensure_public_token_defaults_to_config_store(store):
    token = ensure_public_token("444")
    assert store.data["servers"]["444"][TOKEN_KEY] == token
    assert ensure_public_token("444") == token
    assert ensure_public_token("444", rotate=True) != token


def test_published_flag_requires_enabled_module():
    cfg = _config()["servers"]
    assert public_dashboard_enabled(cfg[PUBLISHED], "xp")
    assert not public_dashboard_enabled(cfg[PRIVATE], "xp")
    assert not public_dashboard_enabled(cfg[PRIVATE], "spotify")
    assert not public_dashboard_enabled(cfg[PUBLISHED], "unknown")
    assert enabled_public_dashboards(cfg[PRIVATE]) == []


def test_public_url_needs_a_token_and_an_enabled_webui_with_base_url():
    cfg = {"webui": {"enabled": True, "baseUrl": "https://x.fr/ "}}
    assert public_dashboard_url({}, TOKEN, "xp") == ""
    assert public_dashboard_url({"webui": {"enabled": True}}, TOKEN, "xp") == ""
    assert public_dashboard_url({"webui": {"baseUrl": "https://x.fr"}}, TOKEN, "xp") == ""
    assert public_dashboard_url(cfg, "", "xp") == ""
    assert public_dashboard_url(cfg, TOKEN, "spotify") == f"https://x.fr/public/{TOKEN}/spotify"


# --- XP ---------------------------------------------------------------------


def test_xp_stats_drop_ids_and_handles(http):
    resp = http.get(f"/api/public/{TOKEN}/xp/stats?days=7")
    assert resp.status_code == 200
    body = resp.json()
    assert body["days"] == 7
    assert body["summary"]["members"] == 2
    assert [row["display_name"] for row in body["top"]] == ["Alice", "Membre inconnu"]
    assert all("user_id" not in row and "username" not in row for row in body["top"])


def test_xp_stats_validate_range(http):
    assert http.get(f"/api/public/{TOKEN}/xp/stats?days=12").status_code == 400


def test_xp_stats_are_cached_per_window(http):
    http.get(f"/api/public/{TOKEN}/xp/stats?days=7")
    http.get(f"/api/public/{TOKEN}/xp/stats?days=7")
    assert _FakeXpRepo.calls == 1
    http.get(f"/api/public/{TOKEN}/xp/stats?days=30")
    assert _FakeXpRepo.calls == 2


def test_xp_database_error_is_503_and_not_cached(http):
    _FakeXpRepo.error = DatabaseError("MongoDB injoignable")
    resp = http.get(f"/api/public/{TOKEN}/xp/stats")
    assert resp.status_code == 503
    assert "MongoDB" not in resp.json()["detail"]
    _FakeXpRepo.error = None
    assert http.get(f"/api/public/{TOKEN}/xp/stats").status_code == 200


# --- Spotify ----------------------------------------------------------------


def test_spotify_stats_replace_user_ids_with_names(http):
    resp = http.get(f"/api/public/{TOKEN}/spotify/stats")
    assert resp.status_code == 200
    body = resp.json()
    # Configured first name wins over the Discord display name.
    assert body["top_contributors"] == [{"key": "Septime", "count": 1}]
    assert {v["user_id"] for v in body["voters"]} == {"Septime", "huit", "Membre inconnu"}
    assert body["current_poll"]["added_by"] == "Septime"
    assert body["top_artists"] == [{"key": "Artist", "count": 1}]
    raw = resp.text
    assert '"7"' not in raw and '"8"' not in raw and '"9"' not in raw


def test_open_poll_results_stay_hidden(http):
    poll = http.get(f"/api/public/{TOKEN}/spotify/stats").json()["current_poll"]
    # "huit" voted on the open poll: neither the tally nor the voter may leak.
    assert poll == {
        "name": "Artist - Song",
        "date": "2026-01-11",
        "added_by": "Septime",
        "results_hidden": True,
    }


def test_spotify_stats_are_cached(http):
    http.get(f"/api/public/{TOKEN}/spotify/stats")
    http.get(f"/api/public/{TOKEN}/spotify/stats")
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
