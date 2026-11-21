"""The dashboard's "Tester" buttons: a feed read now, a connection tried now (C9)."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.webui.routes.checks as checks
from features.rss.models import RssEntry
from src.core.errors import IntegrationError
from src.core.http import BlockedUrlError
from src.integrations.lldap import LldapClient, LldapError

LLDAP = {
    "enabled": True,
    "lldapUrl": "https://lldap.example.com",
    "lldapLdapUrl": "ldaps://lldap.example.com:6360",
    "lldapBaseDn": "dc=example,dc=com",
    "lldapAdminUsername": "admin",
    "lldapAdminPassword": "hunter2",  # pragma: allowlist secret
}


class Ctx:
    def __init__(self, server=None):
        self.data = {"servers": {"1": server or {}}}

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        return SimpleNamespace(username="alice", user_id="7")

    def require_admin(self, request):  # noqa: ARG002
        return None

    def get_full_config(self):
        return self.data


def endpoint(ctx, method, path):
    router = checks.create_router(ctx)
    return next(r for r in router.routes if r.path == path and method in r.methods).endpoint


def entry(n):
    return RssEntry(
        entry_id=str(n),
        title=f"Article {n}",
        link=f"https://news.example.com/{n}",
        summary="Résumé",
        published=datetime(2026, 11, 20, 10, tzinfo=UTC),
    )


def feed_test(ctx, **body):
    post = endpoint(ctx, "POST", "/api/servers/{server_id}/rss/test")
    return json.loads(asyncio.run(post(None, "1", checks.FeedTestBody(**body))).body)


def test_a_feed_shows_its_latest_entries_as_they_would_be_posted(monkeypatch):
    async def fetch(url):
        assert url == "https://news.example.com/rss"
        return [entry(n) for n in range(5)]

    monkeypatch.setattr(checks, "fetch_feed", fetch)
    out = feed_test(
        Ctx(), url=" https://news.example.com/rss ", label="Actu", template="📰 {label} : {title}"
    )
    assert out["count"] == 5 and len(out["entries"]) == 3
    assert out["entries"][0]["text"] == "📰 Actu : Article 0"
    # No template: the bot's default, as a post would have.
    plain = feed_test(Ctx(), url="https://news.example.com/rss", label="Actu")
    assert plain["entries"][0]["text"] == "**Actu** — [Article 0](https://news.example.com/0)"


@pytest.mark.parametrize(
    ("error", "says"),
    [
        (BlockedUrlError("private", url="http://10.0.0.1/rss"), "publiques"),
        (IntegrationError("boom at http://secret@host"), "ne répond pas"),
    ],
)
def test_a_feed_that_cant_be_read_says_why_without_the_driver_text(monkeypatch, error, says):
    async def fetch(url):  # noqa: ARG001
        raise error

    monkeypatch.setattr(checks, "fetch_feed", fetch)
    with pytest.raises(HTTPException) as refused:
        feed_test(Ctx(), url="http://10.0.0.1/rss")
    assert refused.value.status_code == 422
    assert says in refused.value.detail and "secret" not in refused.value.detail


def check(ctx, module):
    post = endpoint(ctx, "POST", "/api/servers/{server_id}/modules/{module_name}/check")
    return json.loads(asyncio.run(post(None, "1", module)).body)


def test_lldap_is_tried_with_the_saved_settings(monkeypatch):
    seen = []

    async def list_groups(self, settings):  # noqa: ARG001
        seen.append(settings)
        return {1: "a", 2: "b"}

    monkeypatch.setattr(LldapClient, "list_groups", list_groups)
    out = check(Ctx({"moduleLldap": LLDAP}), "moduleLldap")
    assert "2 groupe(s)" in out["message"]
    assert seen[0].admin_password == "hunter2"  # pragma: allowlist secret


def test_lldap_says_what_went_wrong(monkeypatch):
    async def list_groups(self, settings):  # noqa: ARG001
        raise LldapError("LLDAP a refusé les identifiants administrateur.")

    monkeypatch.setattr(LldapClient, "list_groups", list_groups)
    with pytest.raises(HTTPException) as refused:
        check(Ctx({"moduleLldap": LLDAP}), "moduleLldap")
    assert refused.value.status_code == 502 and "identifiants" in refused.value.detail
    with pytest.raises(HTTPException) as incomplete:
        check(Ctx({"moduleLldap": {"enabled": True, "lldapUrl": "https://x"}}), "moduleLldap")
    assert incomplete.value.status_code == 422
    with pytest.raises(HTTPException) as unsaved:
        check(Ctx(), "moduleLldap")
    assert unsaved.value.status_code == 422


def test_only_listed_modules_are_checked():
    with pytest.raises(HTTPException) as missing:
        check(Ctx({"moduleXp": {"enabled": True}}), "moduleXp")
    assert missing.value.status_code == 404
    get = endpoint(Ctx(), "GET", "/api/connection-checks")
    assert json.loads(asyncio.run(get(None)).body) == ["moduleLldap"]
