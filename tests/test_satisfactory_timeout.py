"""A Satisfactory server that never answers can't hold the refresh (B11)."""

import socket
import time

import pytest
from pyfactorybridge.exceptions import ServerError

import extensions.satisfactory as satisfactory


@pytest.fixture
def silent_server():
    """A TCP port that accepts connections and never answers."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    yield f"127.0.0.1:{server.getsockname()[1]}"
    server.close()


def test_the_client_gives_up_on_a_silent_server(silent_server, monkeypatch):
    monkeypatch.setattr(satisfactory, "REQUEST_TIMEOUT", (0.5, 0.5))
    started = time.monotonic()
    with pytest.raises(ServerError):
        satisfactory._build_api(silent_server, "token")
    assert time.monotonic() - started < 5


def test_every_request_gets_the_timeout(monkeypatch):
    seen = {}

    def fake_request(self, method, url, *args, **kwargs):
        seen.update(kwargs)
        raise ConnectionError

    monkeypatch.setattr(satisfactory.requests.Session, "request", fake_request)
    with pytest.raises(ConnectionError):
        satisfactory._TimeoutSession().post("https://example.invalid/")
    assert seen["timeout"] == satisfactory.REQUEST_TIMEOUT


async def test_a_call_is_bounded_even_if_a_request_hangs(monkeypatch):
    monkeypatch.setattr(satisfactory, "CALL_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(TimeoutError):
        await satisfactory._call(time.sleep, 1)


async def test_the_client_is_built_with_the_token(monkeypatch):
    calls = []

    def fake_request(self, method, url, *args, **kwargs):
        calls.append(kwargs["json"]["function"])

        class Response:
            def json(self):
                return {"data": {"health": "healthy"}}

        return Response()

    monkeypatch.setattr(satisfactory.requests.Session, "request", fake_request)
    api = await satisfactory._call(satisfactory._build_api, "host:7777", "tok")
    assert api.URL == "https://host:7777/api/v1/"
    assert calls == ["HealthCheck"]
    assert api.auth is not None
