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
    assert json.loads(asyncio.run(get(None)).body) == [
        "moduleLldap",
        "moduleMinecraft",
        "moduleSatisfactory",
        "moduleUptime",
        "moduleYoutube",
    ]


# ── P16: the checks whose clients moved out of their extension ────────────

SATISFACTORY = {
    "enabled": True,
    "satisfactoryServerIp": "sf.example.com",
    "satisfactoryServerToken": "tok",
    "satisfactoryServerCertificateSha256": "ab" * 32,
}


def test_satisfactory_is_reached_through_its_pinned_certificate(monkeypatch):
    built = []

    class Api:
        def query_server_state(self):
            return {"serverGameState": {"numConnectedPlayers": 3, "techTier": 5}}

    def build_api(address, token, fingerprint):
        built.append((address, token, fingerprint))
        return Api()

    monkeypatch.setattr(checks.satisfactory, "build_api", build_api)
    out = check(Ctx({"moduleSatisfactory": SATISFACTORY}), "moduleSatisfactory")
    assert out["message"] == "Serveur joignable : 3 joueur(s) connecté(s), palier 5."
    assert built == [("sf.example.com:7777", "tok", "ab" * 32)]


def test_satisfactory_without_a_fingerprint_never_sends_the_token(monkeypatch):
    monkeypatch.setattr(checks.satisfactory, "build_api", lambda *a: pytest.fail("contacted"))
    unpinned = {**SATISFACTORY, "satisfactoryServerCertificateSha256": ""}
    with pytest.raises(HTTPException) as refused:
        check(Ctx({"moduleSatisfactory": unpinned}), "moduleSatisfactory")
    assert refused.value.status_code == 422 and "empreinte" in refused.value.detail

    def down(*_):
        raise ConnectionError("refused")

    monkeypatch.setattr(checks.satisfactory, "build_api", down)
    with pytest.raises(HTTPException) as unreachable:
        check(Ctx({"moduleSatisfactory": SATISFACTORY}), "moduleSatisfactory")
    assert unreachable.value.status_code == 502 and "refused" not in unreachable.value.detail


def test_minecraft_answers_with_its_players(monkeypatch):
    import mcstatus

    status = SimpleNamespace(
        players=SimpleNamespace(online=2, max=20), version=SimpleNamespace(name="1.21")
    )
    looked_up = []

    class Server:
        @staticmethod
        def lookup(address):
            looked_up.append(address)
            return Server()

        def status(self):
            return status

    monkeypatch.setattr(mcstatus, "JavaServer", Server)
    settings = {
        "enabled": True,
        "minecraftUrl": "mc.example.com",
        "minecraftIp": "",
        "minecraftPort": "",
    }
    out = check(Ctx({"moduleMinecraft": settings}), "moduleMinecraft")
    assert out["message"] == "Serveur joignable : 2/20 joueur(s), version 1.21."
    assert looked_up == ["mc.example.com"]
    with pytest.raises(HTTPException) as incomplete:
        check(Ctx({"moduleMinecraft": {"enabled": True}}), "moduleMinecraft")
    assert incomplete.value.status_code == 422


def test_uptime_kuma_reads_the_servers_own_instance(monkeypatch, store_config):
    """P37: the server's URL and key, not the bot's (whose section only pushes its heartbeat)."""
    store_config({"config": {"uptimeKuma": {"uptimeKumaUrl": "https://kuma.example.com"}}})
    with pytest.raises(HTTPException) as unset:
        check(Ctx({"moduleUptime": {"enabled": True}}), "moduleUptime")
    assert unset.value.status_code == 422

    async def states(settings):
        assert (settings.base_url, settings.api_key) == ("https://kuma.ici", "k")
        return {1: object(), 2: object()}

    monkeypatch.setattr(checks, "fetch_monitor_states", states)
    own = {"enabled": True, "uptimeKumaUrl": "https://kuma.ici", "uptimeKumaApiKey": "k"}
    out = check(Ctx({"moduleUptime": own}), "moduleUptime")
    assert out["message"] == "Uptime Kuma répond : 2 sonde(s) visible(s)."


def test_youtube_names_each_channel_s_latest_video(monkeypatch):
    async def uploads(settings, handle):
        assert settings["youtubeApiKey"] == "k"  # the server's own key (P37)
        if handle == "@perdu":
            raise KeyError("items")
        return f"UU-{handle}"

    async def latest(settings, playlist):
        return {"title": f"Vidéo de {playlist}"}

    monkeypatch.setattr(checks.youtube, "uploads_playlist", uploads)
    monkeypatch.setattr(checks.youtube, "latest_upload", latest)
    settings = {
        "enabled": True,
        "youtubeApiKey": "k",
        "youtubeApiUrl": "https://yt/v3",
        "youtubeChannelList": {"@chaine": {}, "@perdu": {}},
    }
    out = check(Ctx({"moduleYoutube": settings}), "moduleYoutube")
    assert "@chaine : « Vidéo de UU-@chaine »" in out["message"]
    assert "@perdu : introuvable" in out["message"]
