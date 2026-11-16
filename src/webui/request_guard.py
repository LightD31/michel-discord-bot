"""Two checks on every dashboard request, before any route runs.

* **Cross-site writes are refused.** A ``POST`` / ``PUT`` / ``PATCH`` /
  ``DELETE`` under ``/api/`` (and ``/auth/logout``) must come from the
  dashboard's own pages: ``Sec-Fetch-Site`` must be ``same-origin`` (or
  ``none``, typed by the user), else the ``Origin`` header must name the host
  the request was sent to. ``SameSite=Lax`` cookies already stop most other
  sites, but not a neighbouring subdomain, and the body-less POSTs
  (``…/rotate``, ``…/publish``, ``/api/reload…``, logout) have no JSON
  content type to fall back on. A request with neither header (a script, an
  old browser) passes: forging one from a page needs a browser, which sends
  them.
* **A forwarding header from an unknown peer is reported, once.** Behind a
  reverse proxy that isn't in ``webui.trustedProxies`` (the gateway of a
  Docker compose network, say), every visitor shares one rate-limit bucket
  and nothing is seen as HTTPS (no ``Secure`` cookie, no HSTS). The first such
  request logs a warning naming the peer to add.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request

from src.core import logging as logutil
from src.webui.mutation_limit import MUTATING_METHODS
from src.webui.ratelimit import TrustedProxies

logger = logutil.init_logger("webui.request_guard")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

CROSS_SITE_REFUSAL = "Requête refusée : elle ne vient pas du dashboard."
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})


def _guarded(scope: Scope) -> bool:
    path = scope["path"]
    return scope["method"] in MUTATING_METHODS and (
        path.startswith("/api/") or path == "/auth/logout"
    )


def _own_host(request: Request, trusted: TrustedProxies | None) -> str:
    """The host the client sent the request to (``X-Forwarded-Host`` from our proxies)."""
    peer = request.client.host if request.client else None
    if trusted is not None and trusted.trusts(peer):
        forwarded = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        if forwarded:
            return forwarded.lower()
    return request.headers.get("host", "").lower()


def is_cross_site(request: Request, trusted: TrustedProxies | None = None) -> bool:
    """Whether a browser sent *request* from a page that isn't the dashboard."""
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site:
        return fetch_site.lower() not in _ALLOWED_FETCH_SITES
    origin = request.headers.get("origin")
    if not origin:
        return False
    if origin == "null":  # a sandboxed frame, a file:// page, a redirect chain
        return True
    return urlsplit(origin).netloc.lower() != _own_host(request, trusted)


class RequestGuardMiddleware:
    """403 on cross-site writes; one warning for forwarding headers from an unknown peer."""

    def __init__(self, app: ASGIApp, trusted_proxies: TrustedProxies | None = None) -> None:
        self.app = app
        self.trusted_proxies = trusted_proxies or TrustedProxies()
        self.warned_untrusted_proxy = False

    def _warn_untrusted_proxy(self, request: Request) -> None:
        if self.warned_untrusted_proxy:
            return
        if not any(h in request.headers for h in ("x-forwarded-for", "x-forwarded-proto")):
            return
        peer = request.client.host if request.client else None
        if self.trusted_proxies.trusts(peer):
            return
        self.warned_untrusted_proxy = True
        logger.warning(
            "Forwarding headers received from %s, which is not in webui.trustedProxies: "
            "they are ignored, so every visitor shares one login rate limit and the "
            "dashboard never sees HTTPS (no Secure cookie, no HSTS). If %s is your "
            "reverse proxy (with Docker compose, the network's gateway), add it there.",
            peer,
            peer,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        self._warn_untrusted_proxy(request)
        if not _guarded(scope) or not is_cross_site(request, self.trusted_proxies):
            await self.app(scope, receive, send)
            return

        logger.warning(
            "Refused a cross-site %s %s (Origin %r, Sec-Fetch-Site %r)",
            scope["method"],
            scope["path"],
            request.headers.get("origin"),
            request.headers.get("sec-fetch-site"),
        )
        body = json.dumps({"detail": CROSS_SITE_REFUSAL}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = ["CROSS_SITE_REFUSAL", "RequestGuardMiddleware", "is_cross_site"]
