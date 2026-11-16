"""The Spotify OAuth flow: a Secure state cookie, and no free text sent back (S09, S15)."""

from types import SimpleNamespace

from starlette.requests import Request

import src.webui.routes.spotify as spotify_routes
from src.webui.ratelimit import TrustedProxies


class Ctx:
    trusted_proxies = TrustedProxies(["127.0.0.1"])

    def require_developer(self, request):
        return None


def endpoint(path):
    router = spotify_routes.create_router(Ctx())
    return next(r.endpoint for r in router.routes if r.path == path)


def request(*, forwarded_proto=None, peer="127.0.0.1", query=b""):
    headers = [(b"host", b"michel.example")]
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "query_string": query,
            "headers": headers,
            "client": (peer, 5000),
            "server": ("michel.example", 80),
        }
    )


def fake_oauth(**kwargs):
    return SimpleNamespace(
        client_id="id", client_secret="x", get_authorize_url=lambda state: "https://accounts/x"
    )


async def test_the_state_cookie_is_secure_behind_an_https_proxy(store_config, monkeypatch):
    store_config({"config": {"webui": {"baseUrl": "https://michel.example"}}, "servers": {}})
    monkeypatch.setattr(spotify_routes, "build_oauth", fake_oauth)
    start = endpoint("/spotify/auth/start")

    secure = await start(request(forwarded_proto="https"))
    assert "secure" in secure.headers["set-cookie"].lower()
    plain = await start(request())
    assert "secure" not in plain.headers["set-cookie"].lower()


async def test_the_result_page_sends_no_text_back_to_the_dashboard(store_config):
    store_config({"config": {"webui": {"baseUrl": "https://michel.example"}}, "servers": {}})
    callback = endpoint("/spotify/auth/callback")

    page = await callback(request(), code="", state="", error="<b>Pirate</b> appelle le 06")
    body = page.body.decode()
    assert "Pirate" not in body
    assert "(inconnue)" in body
    assert "msg=" not in body
    assert 'href="/#/global?section=spotify&spotify_auth=error"' in body
