"""The bot's Twitch account, connected from the dashboard (P29).

Without a token, twitchAPI would open a browser on the bot's machine; the
dashboard's OAuth flow writes the file the library reads instead.
"""

import json
import stat
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.requests import Request

import src.webui.routes.twitch as twitch_routes
from src.integrations import twitch_auth
from src.webui.ratelimit import TrustedProxies

BASE = "https://michel.example"
APP = {"twitchClientId": "app-id", "twitchClientSecret": "app-secret"}  # pragma: allowlist secret


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "twitchcreds.json"
    monkeypatch.setattr(twitch_auth, "TOKEN_PATH", path)
    return path


@pytest.fixture
def configured(store_config):
    store_config({"config": {"webui": {"baseUrl": BASE}, "twitch": APP}, "servers": {}})


class Ctx:
    trusted_proxies = TrustedProxies(["127.0.0.1"])
    bot = None

    def __init__(self):
        self.session = SimpleNamespace(username="dev", user_id="1")

    def require_developer(self, request):
        return self.session

    def get_extension_module_paths(self):
        return []


def endpoint(path, ctx=None):
    router = twitch_routes.create_router(ctx or Ctx())
    return next(r.endpoint for r in router.routes if r.path == path)


def request(*, cookie: str | None = None, forwarded_proto=None):
    headers = [(b"host", b"michel.example")]
    if cookie:
        headers.append((b"cookie", f"{twitch_routes.STATE_COOKIE}={cookie}".encode()))
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 5000),
            "server": ("michel.example", 80),
        }
    )


def test_the_authorize_url_asks_for_the_scope_and_comes_back_here():
    url = twitch_auth.authorize_url("app-id", f"{BASE}/twitch/auth/callback", "st4te")
    query = parse_qs(urlsplit(url).query)
    assert url.startswith("https://id.twitch.tv/oauth2/authorize?")
    assert query["client_id"] == ["app-id"] and query["state"] == ["st4te"]
    assert query["redirect_uri"] == [f"{BASE}/twitch/auth/callback"]
    assert query["scope"] == ["user:read:subscriptions"]
    assert "client_secret" not in query  # never in a URL


def test_the_tokens_are_kept_private_in_the_libraries_format(tmp_path):
    path = tmp_path / "data" / "twitchcreds.json"
    twitch_auth.save_tokens("tok", "ref", path=path)
    assert json.loads(path.read_text()) == {"token": "tok", "refresh": "ref"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert twitch_auth.stored_tokens(path) == {"token": "tok", "refresh": "ref"}
    path.write_text("{}")
    assert twitch_auth.stored_tokens(path) is None


async def test_the_library_is_never_let_open_a_browser():
    with pytest.raises(twitch_auth.TwitchNotConnected, match="réglages globaux"):
        await twitch_auth.refuse_browser_login(None, twitch_auth.SCOPES)


async def test_the_code_exchange_sends_the_secret_in_the_body(monkeypatch):
    sent = {}

    class Response:
        status = 200

        async def json(self, content_type=None):
            return {"access_token": "tok", "refresh_token": "ref"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Session:
        def post(self, url, data):
            sent.update(url=url, data=data)
            return Response()

    async def session():
        return Session()

    monkeypatch.setattr(twitch_auth.http_client, "session", session)
    assert await twitch_auth.exchange_code("id", "secret", "c0de", "https://cb") == ("tok", "ref")
    assert "secret" not in sent["url"] and sent["data"]["client_secret"] == "secret"
    assert sent["data"]["grant_type"] == "authorization_code"


async def test_start_sets_a_secure_state_cookie_behind_https(configured):
    start = endpoint("/twitch/auth/start")
    response = await start(request(forwarded_proto="https"))
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    cookie = response.headers["set-cookie"]
    assert f"{twitch_routes.STATE_COOKIE}={state}" in cookie and "secure" in cookie.lower()


async def test_start_needs_the_app_credentials(store_config):
    store_config({"config": {"webui": {"baseUrl": BASE}}, "servers": {}})
    with pytest.raises(Exception) as refused:
        await endpoint("/twitch/auth/start")(request())
    assert refused.value.status_code == 400 and "section Twitch" in refused.value.detail


async def test_a_callback_with_another_state_keeps_nothing(configured, token_file):
    callback = endpoint("/twitch/auth/callback")
    page = await callback(request(cookie="mine"), code="c", state="theirs", error="")
    assert page.status_code == 400 and not token_file.exists()
    page = await callback(request(cookie="mine"), code="", state="", error="<b>x</b>")
    assert "(inconnue)" in page.body.decode() and "<b>x" not in page.body.decode()


async def test_a_good_callback_keeps_the_tokens_and_restarts_their_users(
    configured, token_file, monkeypatch
):
    async def exchange(client_id, secret, code, redirect_uri):
        assert (client_id, code, redirect_uri) == ("app-id", "c0de", f"{BASE}/twitch/auth/callback")
        return "tok", "ref"

    reloaded, audited = [], []

    async def run_extension_op(ctx, action, ext):
        reloaded.append((action, ext))

    async def record(guild_id, session, action, **kwargs):
        audited.append(action)

    monkeypatch.setattr(twitch_auth, "exchange_code", exchange)
    monkeypatch.setattr(twitch_routes, "run_extension_op", run_extension_op)
    monkeypatch.setattr(twitch_routes.audit, "record", record)
    ctx = Ctx()
    ctx.bot = object()
    ctx.get_extension_module_paths = lambda: ["extensions.twitch", "extensions.xp"]

    page = await endpoint("/twitch/auth/callback", ctx)(
        request(cookie="s"), code="c0de", state="s", error=""
    )
    assert page.status_code == 200
    assert 'href="/#/global?section=twitch&twitch_auth=ok"' in page.body.decode()
    assert json.loads(token_file.read_text()) == {"token": "tok", "refresh": "ref"}
    assert reloaded == [("reload", "extensions.twitch")]  # streamlabs isn't loaded here
    assert audited == ["twitch.connect"]


async def test_the_status_says_what_is_missing(configured, token_file):
    status = await endpoint("/api/twitch/status")(request())
    body = json.loads(status.body)
    assert body["app_configured"] is True and body["connected"] is False
    assert body["callback_url"] == f"{BASE}/twitch/auth/callback"
