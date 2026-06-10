"""Tests for the auth-endpoint rate limiter."""

from src.webui import ratelimit
from src.webui.ratelimit import RateLimiter, client_ip


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def make_limiter(monkeypatch, max_requests=3, window=60.0):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit, "time", clock)
    return RateLimiter(max_requests, window), clock


def test_allows_up_to_limit_then_blocks(monkeypatch):
    limiter, _ = make_limiter(monkeypatch)
    assert limiter.check("ip") == 0.0
    assert limiter.check("ip") == 0.0
    assert limiter.check("ip") == 0.0
    assert limiter.check("ip") > 0.0


def test_keys_are_independent(monkeypatch):
    limiter, _ = make_limiter(monkeypatch, max_requests=1)
    assert limiter.check("a") == 0.0
    assert limiter.check("a") > 0.0
    assert limiter.check("b") == 0.0


def test_window_expiry_frees_budget(monkeypatch):
    limiter, clock = make_limiter(monkeypatch, max_requests=2, window=60.0)
    limiter.check("ip")
    limiter.check("ip")
    assert limiter.check("ip") > 0.0
    clock.now += 61.0
    assert limiter.check("ip") == 0.0


def test_refused_attempts_do_not_extend_the_block(monkeypatch):
    limiter, clock = make_limiter(monkeypatch, max_requests=1, window=60.0)
    limiter.check("ip")
    for _ in range(5):
        assert limiter.check("ip") > 0.0
    clock.now += 60.5
    assert limiter.check("ip") == 0.0


def test_retry_after_reflects_window_remaining(monkeypatch):
    limiter, clock = make_limiter(monkeypatch, max_requests=1, window=60.0)
    limiter.check("ip")
    clock.now += 20.0
    retry_after = limiter.check("ip")
    assert 39.0 <= retry_after <= 41.0


def test_idle_keys_are_pruned(monkeypatch):
    limiter, clock = make_limiter(monkeypatch, max_requests=2, window=60.0)
    limiter.check("old")
    clock.now += 120.0
    limiter.check("new")
    assert "old" not in limiter._hits


class FakeClient:
    host = "10.0.0.5"


class FakeRequest:
    def __init__(self, headers=None, client=FakeClient()):
        self.headers = headers or {}
        self.client = client


def test_client_ip_prefers_forwarded_for():
    request = FakeRequest(headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"})
    assert client_ip(request) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_peer():
    assert client_ip(FakeRequest()) == "10.0.0.5"
    assert client_ip(FakeRequest(headers={"x-forwarded-for": "  "})) == "10.0.0.5"
    assert client_ip(FakeRequest(client=None)) == "unknown"
