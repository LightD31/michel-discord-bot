"""Tiny in-memory sliding-window rate limiter for auth endpoints.

The dashboard binds to loopback behind a reverse proxy, so the client key
honors the leftmost ``X-Forwarded-For`` entry when present and falls back to
the socket peer. State is process-local (one uvicorn worker), which is exactly
the deployment shape of the Web UI.
"""

import time
from collections import deque

from fastapi import Request


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


def client_ip(request: Request) -> str:
    """Client key for rate limiting (leftmost X-Forwarded-For, else socket peer)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"
