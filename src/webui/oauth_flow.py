"""What the dashboard's OAuth flows share (Spotify, Twitch; P29).

A flow is two routes: ``…/auth/start`` redirects the user to the service
with a random ``state`` kept in a short-lived cookie (prefixed with what the
callback must know, as Spotify's names the server it connects, P36), and ``…/auth/callback``
checks that state before exchanging the code, then answers with a small page
that sends them back to the dashboard. The callback URL is built from
``webui.baseUrl``, the one source the service and both routes agree on.
"""

from __future__ import annotations

import html
import re
import secrets
from collections.abc import Callable

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.core.config import config_store
from src.webui.context import WebUIContext
from src.webui.ratelimit import request_is_https


def callback_url(path: str) -> str:
    """``webui.baseUrl`` + *path*; 400 when the base URL isn't set."""
    base = config_store.get().get("config", {}).get("webui", {}).get("baseUrl", "")
    if not base:
        raise HTTPException(
            status_code=400,
            detail=(
                "Configure d'abord webui.baseUrl (section « Dashboard Web ») "
                "pour que le callback OAuth pointe vers le dashboard."
            ),
        )
    return f"{base.rstrip('/')}{path}"


def redirect_with_state(
    ctx: WebUIContext,
    request: Request,
    url_for_state: Callable[[str], str],
    cookie: str,
    *,
    prefix: str = "",
) -> RedirectResponse:
    """Redirect to ``url_for_state(state)``, the state kept 10 minutes in *cookie*.

    The state is *prefix* + a random token: the callback reads the prefix back,
    and the cookie's match proves the browser that started the flow sent it.
    """
    state = prefix + secrets.token_urlsafe(24)
    response = RedirectResponse(url=url_for_state(state))
    response.set_cookie(
        cookie,
        state,
        httponly=True,
        max_age=600,
        samesite="lax",
        secure=request_is_https(request, ctx.trusted_proxies),
    )
    return response


def state_matches(request: Request, cookie: str, state: str) -> bool:
    expected = request.cookies.get(cookie)
    if not expected:
        return False
    return secrets.compare_digest(expected.encode(), state.encode())


def error_word(error: str) -> str:
    """A service's error code, echoed only when it looks like one (``access_denied``)."""
    return error if re.fullmatch(r"[a-z_]{1,40}", error) else "inconnue"


def result_page(*, service: str, ok: bool, message: str, back: str) -> HTMLResponse:
    """Tiny page that says how it went and links back to the dashboard.

    *back* is the dashboard route to go back to, ending with the outcome's
    parameter (``global?section=twitch&twitch_auth``): the link is
    ``/#/<back>=ok`` or ``=error``. The calling route builds it from constants
    and checked ids only.

    Only the outcome goes back to the dashboard, never a text: a link carrying
    its own message would let anyone show any toast on the dashboard's origin.
    """
    title = f"{service} connecté" if ok else f"Échec de connexion {service}"
    color = "#1ed760" if ok else "#e53e3e"
    status = "ok" if ok else "error"
    body = f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{
  font-family: system-ui, sans-serif;
  background: #18181b; color: #e4e4e7;
  display: flex; align-items: center; justify-content: center;
  height: 100vh; margin: 0;
}}
.card {{
  background: #27272a; padding: 2rem; border-radius: 12px;
  border-left: 4px solid {color}; max-width: 480px; text-align: center;
}}
.card h1 {{ margin: 0 0 0.5rem; color: {color}; font-size: 1.2rem; }}
.card p {{ margin: 0; color: #a1a1aa; font-size: 0.9rem; }}
.card a {{
  display: inline-block; margin-top: 1.5rem; padding: 0.5rem 1rem;
  background: #3f3f46; color: #fafafa; border-radius: 6px;
  text-decoration: none; font-size: 0.85rem;
}}
.card a:hover {{ background: #52525b; }}
</style></head>
<body><div class="card">
<h1>{html.escape(title)}</h1>
<p>{html.escape(message)}</p>
<a href="/#/{back}={status}">Retour au dashboard</a>
</div></body></html>"""
    return HTMLResponse(content=body, status_code=200 if ok else 400)


__all__ = [
    "callback_url",
    "error_word",
    "redirect_with_state",
    "result_page",
    "state_matches",
]
