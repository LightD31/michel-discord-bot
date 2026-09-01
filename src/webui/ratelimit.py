"""Tiny in-memory sliding-window rate limiter for auth endpoints.

State is process-local (one uvicorn worker), which is exactly the deployment
shape of the Web UI.

Identifying the client is the delicate half. The dashboard normally sits behind
a reverse proxy that sets ``X-Forwarded-For``, so the socket peer alone would
lump every visitor under the proxy's address — but the header is trivially
forged, and believing it unconditionally lets anyone reset their own rate-limit
bucket by varying one header. :class:`TrustedProxies` settles which requests may
be believed: only those whose peer really is one of our proxies.
"""

import ipaddress
import os
import time
from collections import deque
from collections.abc import Sequence

from fastapi import Request

from src.core import logging as _logging

logger = _logging.init_logger(os.path.basename(__file__))

# Reverse proxies in the supported deployments are local to the host
# (docker-compose publishes the port on loopback) or sit on a private container
# network. A request arriving from a public address is not one of our proxies,
# so its forwarding headers are ignored. Override with ``webui.trustedProxies``.
DEFAULT_TRUSTED_PROXIES: tuple[str, ...] = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "fc00::/7",
)


class TrustedProxies:
    """Decides whether a request's ``X-Forwarded-*`` headers may be believed."""

    def __init__(self, networks: Sequence[str] | None = None) -> None:
        entries = networks if networks else DEFAULT_TRUSTED_PROXIES
        self._networks = []
        for entry in entries:
            try:
                self._networks.append(ipaddress.ip_network(entry.strip(), strict=False))
            except ValueError:
                logger.warning("Ignoring invalid webui.trustedProxies entry %r", entry)

    def trusts(self, address: str | None) -> bool:
        """True if *address* is one of the proxies we put in front of ourselves."""
        if not address:
            return False
        try:
            parsed = ipaddress.ip_address(address.strip())
        except ValueError:
            return False
        return any(parsed in network for network in self._networks)


def client_ip(request: Request, trusted: TrustedProxies | None = None) -> str:
    """Best-effort client identity for rate limiting.

    With no *trusted* resolver the socket peer is used and forwarding headers
    are ignored outright. Otherwise the ``X-Forwarded-For`` chain is walked from
    the right, skipping our own proxies, and the first address they did not add
    is returned — the leftmost entry is attacker-controlled, since a client may
    send its own ``X-Forwarded-For`` that the proxy then appends to.
    """
    peer = request.client.host if request.client else None
    if trusted is None or not trusted.trusts(peer):
        return peer or "unknown"

    chain = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",")]
    for candidate in reversed([part for part in chain if part]):
        if not trusted.trusts(candidate):
            return candidate
    # The whole chain is our own infrastructure (or the header is absent).
    return peer or "unknown"


def request_is_https(request: Request, trusted: TrustedProxies | None = None) -> bool:
    """Whether the request reached the *client* over HTTPS.

    ``X-Forwarded-Proto`` is honored only from a trusted proxy; otherwise the
    scheme of the connection we actually accepted is authoritative.
    """
    peer = request.client.host if request.client else None
    if trusted is not None and trusted.trusts(peer):
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if forwarded:
            return forwarded == "https"
    return request.url.scheme == "https"


class RateLimiter:
    """Allow at most *max_requests* per *window_seconds* for each key."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> float:
        """Record a hit for *key*; return 0 if allowed, else seconds to wait.

        Refused attempts are not recorded, so a client that backs off for the
        advertised delay is let through again.
        """
        now = time.monotonic()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
        cutoff = now - self.window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return max(hits[0] + self.window_seconds - now, 0.0) or 1.0
        hits.append(now)
        self._prune(cutoff)
        return 0.0

    def _prune(self, cutoff: float) -> None:
        """Drop keys whose hits have all aged out, so the dict stays bounded."""
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]
