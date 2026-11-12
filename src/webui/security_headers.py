"""Response headers that harden every Web UI page and API response.

A pure ASGI middleware rather than ``@app.middleware("http")``: it only edits
the ``http.response.start`` message, so streamed responses (the SSE log stream)
pass through untouched.

What it sets, and why:

- ``Content-Security-Policy`` (:data:`CONTENT_SECURITY_POLICY`) plus
  ``X-Frame-Options: DENY``. Scripts run only from the dashboard's own files
  and the pinned Chart.js build: no inline script, no inline handler, no
  ``eval``, so markup injected through a value the SPA forgot to escape
  cannot run code. The SPA's script is ``/static/app.js`` and its controls
  name an action (``data-click="…"``) dispatched by one listener. Styles may
  be inline (the markup carries ``style`` attributes; CSS cannot run code).
  Images may come from any HTTPS host (guild icons, avatars, embed previews
  of configured URLs); requests (``fetch``, the log stream) only go to the
  dashboard itself. No other site can frame the dashboard (clickjacking a
  "save" or "rotate link" button), inject a ``<base>``, load plugins or
  receive a form.
- ``X-Content-Type-Options: nosniff`` — responses are taken as the type they declare.
- ``Referrer-Policy: same-origin`` — the public dashboards carry their secret
  token in the path, so full URLs must never leave the origin (the lazily
  loaded Chart.js CDN would otherwise see them under some browser defaults).
- ``Strict-Transport-Security`` — only on requests that really arrived over
  HTTPS (``X-Forwarded-Proto`` believed from trusted proxies only), so a plain
  HTTP deployment is never pinned to HTTPS by mistake.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.requests import Request

from src.webui.ratelimit import TrustedProxies, request_is_https

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# Chart.js, lazily loaded by the XP stats page: the exact pinned file, which
# the SPA also loads with its SRI digest. Bump both with the SPA's CHARTJS_SRC.
CHARTJS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        f"script-src 'self' {CHARTJS_URL}",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' https: data:",
        "connect-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "object-src 'none'",
        "form-action 'self'",
    )
)

BASE_HEADERS: tuple[tuple[str, str], ...] = (
    ("content-security-policy", CONTENT_SECURITY_POLICY),
    ("x-frame-options", "DENY"),
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "same-origin"),
)
HSTS_HEADER = ("strict-transport-security", "max-age=31536000")


class SecurityHeadersMiddleware:
    """Add :data:`BASE_HEADERS` (and HSTS over HTTPS) to every HTTP response.

    A header the route already set is left alone.
    """

    def __init__(self, app: ASGIApp, trusted_proxies: TrustedProxies | None = None) -> None:
        self.app = app
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = list(BASE_HEADERS)
        if request_is_https(Request(scope), self.trusted_proxies):
            extra.append(HSTS_HEADER)
        encoded = [(name.encode("latin-1"), value.encode("latin-1")) for name, value in extra]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((name, value) for name, value in encoded if name not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


__all__ = [
    "BASE_HEADERS",
    "CHARTJS_URL",
    "CONTENT_SECURITY_POLICY",
    "HSTS_HEADER",
    "SecurityHeadersMiddleware",
]
