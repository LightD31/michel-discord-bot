"""The Spotify OAuth flow: a Secure state cookie, and no free text sent back (S09, S15).

Each server connects its own account (P36): only its admins start the flow,
and the callback checks again for the server the state names.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import src.webui.routes.spotify as spotify_routes
from src.webui.ratelimit import TrustedProxies


class Ctx:
    trusted_proxies = TrustedProxies(["127.0.0.1"])

    def __init__(self, manages=("123",)):
        self.manages = set(manages)

    async def require_guild_admin(self, request, guild_id):
        if str(guild_id) not in self.manages:
            raise HTTPException(status_code=403, detail="no")
        return SimpleNamespace(username="camille", user_id="5")


def endpoint(path, ctx=None):
    router = spotify_routes.create_router(ctx or Ctx())
    return next(r.endpoint for r in router.routes if r.path == path)


def request(*, forwarded_proto=None, peer="127.0.0.1", query=b"", cookie=None):
    headers = [(b"host", b"michel.example")]
    if cookie:
        headers.append((b"cookie", f"{spotify_routes.STATE_COOKIE}={cookie}".encode()))
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


def fake_oauth(guild_id):
    return SimpleNamespace(
        client_id="id",
        client_secret="x",
        get_authorize_url=lambda state: f"https://accounts.example/x?state={state}",
    )


async def test_the_state_cookie_is_secure_behind_an_https_proxy(store_config, monkeypatch):
    store_config({"config": {"webui": {"baseUrl": "https://michel.example"}}, "servers": {}})
    monkeypatch.setattr(spotify_routes, "build_oauth", fake_oauth)
    start = endpoint("/spotify/auth/start")

    secure = await start(request(forwarded_proto="https"), guild="123")
    assert "secure" in secure.headers["set-cookie"].lower()
    plain = await start(request(), guild="123")
    assert "secure" not in plain.headers["set-cookie"].lower()
    # The state names the server, so the callback knows which account it is.
    assert "state=123." in plain.headers["location"]


async def test_only_the_servers_admins_connect_its_account(store_config, monkeypatch):
    store_config({"config": {"webui": {"baseUrl": "https://michel.example"}}, "servers": {}})
    monkeypatch.setattr(spotify_routes, "build_oauth", fake_oauth)
    with pytest.raises(HTTPException) as refused:
        await endpoint("/spotify/auth/start")(request(), guild="999")
    assert refused.value.status_code == 403

    # A callback whose state names another server: the admin check runs again.
    callback = endpoint("/spotify/auth/callback", Ctx(manages=()))
    with pytest.raises(HTTPException) as refused:
        await callback(request(cookie="123.tok"), code="c", state="123.tok", error="")
    assert refused.value.status_code == 403


async def test_a_state_that_names_no_server_is_refused(store_config):
    store_config({"config": {"webui": {"baseUrl": "https://michel.example"}}, "servers": {}})
    callback = endpoint("/spotify/auth/callback")
    for state in ("tok", "../x.tok", "123."):
        page = await callback(request(cookie=state), code="c", state=state, error="")
        assert page.status_code == 400 and 'href="/#/?spotify_auth=error"' in page.body.decode()


async def test_the_result_page_sends_no_text_back_to_the_dashboard(store_config):
    store_config({"config": {"webui": {"baseUrl": "https://michel.example"}}, "servers": {}})
    callback = endpoint("/spotify/auth/callback")

    page = await callback(request(), code="", state="123.t", error="<b>Pirate</b> appelle le 06")
    body = page.body.decode()
    assert "Pirate" not in body
    assert "(inconnue)" in body
    assert "msg=" not in body
    assert 'href="/#/module/123/moduleSpotify?spotify_auth=error"' in body
