"""Tests for the auth-endpoint rate limiter."""

from src.webui import ratelimit
from src.webui.ratelimit import (
    RateLimiter,
    TrustedProxies,
    client_ip,
    request_is_https,
)


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
    def __init__(self, host="10.0.0.5"):
        self.host = host


class FakeUrl:
    def __init__(self, scheme="http"):
        self.scheme = scheme


class FakeRequest:
    def __init__(self, headers=None, peer="10.0.0.5", scheme="http"):
        self.headers = headers or {}
        self.client = FakeClient(peer) if peer is not None else None
        self.url = FakeUrl(scheme)


TRUSTED = TrustedProxies()


# --- client identity -------------------------------------------------------


def test_forwarded_for_is_ignored_without_a_trust_policy():
    """The default is to believe nothing: the socket peer is the client."""
    request = FakeRequest(headers={"x-forwarded-for": "203.0.113.7"})
    assert client_ip(request) == "10.0.0.5"


def test_forwarded_for_is_honored_from_a_trusted_proxy():
    request = FakeRequest(headers={"x-forwarded-for": "203.0.113.7"}, peer="10.0.0.5")
    assert client_ip(request, TRUSTED) == "203.0.113.7"


def test_forwarded_for_is_ignored_from_an_untrusted_peer():
    """Spoofing the header from the public internet must not move the bucket."""
    request = FakeRequest(headers={"x-forwarded-for": "203.0.113.7"}, peer="198.51.100.9")
    assert client_ip(request, TRUSTED) == "198.51.100.9"


def test_only_the_entries_our_proxies_added_are_trusted():
    """A client that sends its own X-Forwarded-For cannot forge its identity.

    The proxy appends the real peer, so the rightmost entry the client did not
    add is the authoritative one — reading the chain left-to-right would take
    the attacker's value.
    """
    request = FakeRequest(
        headers={"x-forwarded-for": "1.2.3.4, 203.0.113.7, 10.0.0.2"},
        peer="10.0.0.5",
    )
    assert client_ip(request, TRUSTED) == "203.0.113.7"


def test_a_fully_internal_chain_falls_back_to_the_peer():
    request = FakeRequest(headers={"x-forwarded-for": "10.0.0.2, 10.0.0.3"}, peer="10.0.0.5")
    assert client_ip(request, TRUSTED) == "10.0.0.5"


def test_client_ip_falls_back_to_socket_peer():
    assert client_ip(FakeRequest()) == "10.0.0.5"
    assert client_ip(FakeRequest(headers={"x-forwarded-for": "  "}), TRUSTED) == "10.0.0.5"
    assert client_ip(FakeRequest(peer=None)) == "unknown"


def test_garbage_in_the_header_is_not_trusted_as_a_proxy():
    request = FakeRequest(headers={"x-forwarded-for": "not-an-ip"}, peer="10.0.0.5")
    assert client_ip(request, TRUSTED) == "not-an-ip"


# --- trust policy ----------------------------------------------------------


def test_default_policy_trusts_loopback_and_private_ranges_only():
    assert TRUSTED.trusts("127.0.0.1")
    assert TRUSTED.trusts("::1")
    assert TRUSTED.trusts("172.17.0.1")
    assert not TRUSTED.trusts("203.0.113.7")
    assert not TRUSTED.trusts(None)
    assert not TRUSTED.trusts("nonsense")


def test_an_explicit_policy_replaces_the_defaults():
    policy = TrustedProxies(["203.0.113.0/24"])
    assert policy.trusts("203.0.113.7")
    assert not policy.trusts("127.0.0.1")


def test_invalid_entries_are_skipped_not_fatal():
    policy = TrustedProxies(["not-a-network", "127.0.0.1"])
    assert policy.trusts("127.0.0.1")
    assert not policy.trusts("10.0.0.1")


# --- scheme detection ------------------------------------------------------


def test_forwarded_proto_is_honored_from_a_trusted_proxy():
    request = FakeRequest(headers={"x-forwarded-proto": "https"}, peer="127.0.0.1")
    assert request_is_https(request, TRUSTED)


def test_forwarded_proto_from_an_untrusted_peer_is_ignored():
    """A spoofed 'http' must not be able to strip the Secure cookie flag."""
    request = FakeRequest(headers={"x-forwarded-proto": "http"}, peer="203.0.113.7", scheme="https")
    assert request_is_https(request, TRUSTED)


def test_connection_scheme_is_used_without_a_forwarded_header():
    assert request_is_https(FakeRequest(scheme="https"), TRUSTED)
    assert not request_is_https(FakeRequest(scheme="http"), TRUSTED)
