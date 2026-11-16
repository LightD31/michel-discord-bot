"""Cross-site writes are refused, and an untrusted proxy is reported once (S15, S09)."""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.webui.ratelimit import TrustedProxies
from src.webui.request_guard import CROSS_SITE_REFUSAL, RequestGuardMiddleware, is_cross_site


def make_app(trusted: TrustedProxies | None = None):
    app = FastAPI()

    @app.post("/api/reload")
    async def reload():
        return {"ok": True}

    @app.post("/auth/logout")
    async def logout():
        return {"ok": True}

    @app.post("/auth/callback")
    async def other_post():
        return {"ok": True}

    @app.get("/api/thing")
    async def read():
        return {"ok": True}

    app.add_middleware(RequestGuardMiddleware, trusted_proxies=trusted)
    return app


@pytest.fixture
def client():
    return TestClient(make_app(), base_url="https://michel.example")


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "https://sub.michel.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site", "Origin": "https://michel.example"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
@pytest.mark.parametrize("path", ["/api/reload", "/auth/logout"])
def test_a_write_from_another_site_is_refused(client, headers, path):
    resp = client.post(path, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == CROSS_SITE_REFUSAL


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://michel.example"},
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        {},  # a script: no browser, no forged request
    ],
)
def test_a_write_from_the_dashboard_passes(client, headers):
    assert client.post("/api/reload", headers=headers).status_code == 200


def test_reads_and_other_paths_are_not_checked(client):
    assert client.get("/api/thing", headers={"Origin": "https://evil.example"}).status_code == 200
    evil = {"Origin": "https://evil.example"}
    assert client.post("/auth/callback", headers=evil).status_code == 200


def scope_from(peer: str, **headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/reload",
            "client": (peer, 5000),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def test_behind_a_trusted_proxy_the_forwarded_host_counts():
    trusted = TrustedProxies(["172.18.0.1"])
    headers = {
        "Host": "bot:8080",
        "Origin": "https://michel.example",
        "X-Forwarded-Host": "michel.example",
    }
    assert not is_cross_site(scope_from("172.18.0.1", **headers), trusted)
    # From an unknown peer the forwarded host is ignored.
    assert is_cross_site(scope_from("10.0.0.9", **headers), trusted)


def test_forwarding_headers_from_an_unknown_peer_are_reported_once(caplog):
    caplog.set_level(logging.WARNING, logger="webui.request_guard")
    client = TestClient(make_app())
    for _ in range(3):
        client.get("/api/thing", headers={"X-Forwarded-For": "203.0.113.9"})
    warnings = [r for r in caplog.records if "trustedProxies" in r.getMessage()]
    assert len(warnings) == 1
    assert "testclient" in warnings[0].getMessage()


def test_a_trusted_proxy_is_not_reported():
    guard = RequestGuardMiddleware(None, TrustedProxies(["127.0.0.1"]))
    guard._warn_untrusted_proxy(scope_from("127.0.0.1", **{"X-Forwarded-For": "203.0.113.9"}))
    assert guard.warned_untrusted_proxy is False
    guard._warn_untrusted_proxy(scope_from("10.0.0.9", **{"X-Forwarded-For": "203.0.113.9"}))
    assert guard.warned_untrusted_proxy is True
