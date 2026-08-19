"""Tests for the dashboard's short-link (Shlink) endpoints.

The router is mounted on a bare FastAPI app with a stub context so the tests
exercise validation and error mapping without an OAuth session or a live
Shlink instance.
"""

from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.webui.routes.shlink as shlink_routes
from src.integrations.shlink import ShlinkError, ShlinkSettings


@dataclass
class _Session:
    username: str = "dev"
    user_id: str = "42"


class _Ctx:
    """Minimal stand-in for WebUIContext — the router only needs the auth hook."""

    def require_developer(self, request):  # noqa: ARG002 — signature parity
        return _Session()


class _FakeClient:
    def __init__(self):
        self.created: dict | None = None
        self.updated: tuple | None = None
        self.deleted: str | None = None
        self.error: Exception | None = None

    async def list_short_urls(self, **kwargs):
        if self.error:
            raise self.error
        return {
            "data": [
                {
                    "shortCode": "abc",
                    "shortUrl": "https://exemple.link/abc",
                    "longUrl": "https://example.com/article",
                    "title": "Article",
                    "tags": ["michel-bot"],
                    "visitsSummary": {"total": 7},
                    "dateCreated": "2026-08-18T10:00:00+00:00",
                }
            ],
            "pagination": {"currentPage": 1, "pagesCount": 1, "totalItems": 1},
        }

    async def create_short_url(self, long_url, **kwargs):
        if self.error:
            raise self.error
        self.created = {"long_url": long_url, **kwargs}
        return {"shortCode": "new", "shortUrl": "https://exemple.link/new", "longUrl": long_url}

    async def update_short_url(self, short_code, **kwargs):
        if self.error:
            raise self.error
        self.updated = (short_code, kwargs)
        return {
            "shortCode": short_code,
            "shortUrl": f"https://exemple.link/{short_code}",
            "longUrl": kwargs.get("long_url") or "",
        }

    async def delete_short_url(self, short_code, **kwargs):
        if self.error:
            raise self.error
        self.deleted = short_code


@pytest.fixture
def client(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(shlink_routes, "shlink_client", fake)
    monkeypatch.setattr(
        shlink_routes,
        "get_settings",
        lambda: ShlinkSettings(
            {
                "enabled": True,
                "shlinkBaseUrl": "https://exemple.link",
                "shlinkApiKey": "test-key",  # pragma: allowlist secret
                "shlinkTags": ["michel-bot"],
            }
        ),
    )
    app = FastAPI()
    app.include_router(shlink_routes.create_router(_Ctx()))
    return TestClient(app), fake


def test_status_reports_configuration(client):
    http, _ = client
    body = http.get("/api/shlink/status").json()
    assert body["configured"] is True
    assert body["enabled"] is True
    assert body["base_url"] == "https://exemple.link"
    assert body["default_tags"] == ["michel-bot"]


def test_list_projects_shlink_objects(client):
    http, _ = client
    body = http.get("/api/shlink/short-urls").json()
    assert body["short_urls"] == [
        {
            "short_code": "abc",
            "short_url": "https://exemple.link/abc",
            "long_url": "https://example.com/article",
            "title": "Article",
            "tags": ["michel-bot"],
            "domain": "",
            "date_created": "2026-08-18T10:00:00+00:00",
            "visits": 7,
        }
    ]
    assert body["pagination"]["totalItems"] == 1


def test_create_rejects_non_http_url(client):
    http, fake = client
    resp = http.post("/api/shlink/short-urls", json={"long_url": "ftp://example.com/x"})
    assert resp.status_code == 400
    assert fake.created is None


def test_create_forwards_slug_and_tags(client):
    http, fake = client
    resp = http.post(
        "/api/shlink/short-urls",
        json={
            "long_url": " https://example.com/x ",
            "custom_slug": "mon-lien",
            "tags": ["rss", "  ", "news"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["short_url"] == "https://exemple.link/new"
    assert fake.created["long_url"] == "https://example.com/x"
    assert fake.created["custom_slug"] == "mon-lien"
    assert fake.created["tags"] == ["rss", "news"]


def test_update_retargets_short_url(client):
    http, fake = client
    resp = http.patch(
        "/api/shlink/short-urls/abc",
        json={"long_url": "https://example.com/nouveau", "tags": ["news"]},
    )
    assert resp.status_code == 200
    short_code, kwargs = fake.updated
    assert short_code == "abc"
    assert kwargs["long_url"] == "https://example.com/nouveau"
    assert kwargs["tags"] == ["news"]


def test_delete_calls_api(client):
    http, fake = client
    assert http.delete("/api/shlink/short-urls/abc").json() == {"success": True}
    assert fake.deleted == "abc"


def test_client_error_is_surfaced_as_400(client):
    http, fake = client
    fake.error = ShlinkError("Slug déjà pris", status=400)
    resp = http.post("/api/shlink/short-urls", json={"long_url": "https://example.com/x"})
    assert resp.status_code == 400
    assert "Slug" in resp.json()["detail"]


def test_upstream_failure_is_surfaced_as_502(client):
    http, fake = client
    fake.error = ShlinkError("Shlink injoignable")
    assert http.get("/api/shlink/short-urls").status_code == 502
