"""Rate limit on dashboard requests that change something.

Every ``POST`` / ``PUT`` / ``PATCH`` / ``DELETE`` under ``/api/`` counts
against one budget per session (per client IP without a session cookie).
Saves reload extensions and sync commands on the bot loop, and some routes
post to Discord, so a script holding a valid session could otherwise hammer
the bot. A person clicking through the dashboard stays far below the limit.

A pure ASGI middleware, like :mod:`src.webui.security_headers`: it answers 429
before the route runs and passes everything else through untouched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.requests import Request

from src.core import logging as logutil
from src.webui.ratelimit import RateLimiter, TrustedProxies, client_ip

logger = logutil.init_logger("webui.mutation_limit")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Per session: a busy admin saving modules one after another stays well under it.
MUTATION_RATE_LIMIT = 30
MUTATION_RATE_WINDOW_SECONDS = 60.0
SESSION_COOKIE = "michel_session"


class MutationRateLimitMiddleware:
    """429 once a session sends more than the budget of mutating API requests."""

    def __init__(
        self,
        app: ASGIApp,
        trusted_proxies: TrustedProxies | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self.trusted_proxies = trusted_proxies
        self.limiter = limiter or RateLimiter(MUTATION_RATE_LIMIT, MUTATION_RATE_WINDOW_SECONDS)

    def _key(self, request: Request) -> str:
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            # Hashed like the session store does: the raw cookie never sits in memory here.
            return "session:" + hashlib.sha256(cookie.encode()).hexdigest()
        return "ip:" + client_ip(request, self.trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in MUTATING_METHODS
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        retry_after = self.limiter.check(self._key(request))
        if not retry_after:
            await self.app(scope, receive, send)
            return

        logger.warning("Dashboard mutation rate limit hit on %s %s", scope["method"], scope["path"])
        body = json.dumps(
            {"detail": "Trop de modifications d'affilée — réessaie dans un instant."}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"retry-after", str(int(retry_after) + 1).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["MUTATION_RATE_LIMIT", "MutationRateLimitMiddleware"]
