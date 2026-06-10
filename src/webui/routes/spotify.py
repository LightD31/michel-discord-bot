"""Spotify OAuth status + re-authorization endpoints.

Replaces the legacy ``/updatetoken`` slash command. The flow:

1. Admin visits the dashboard's Spotify panel, sees current token status
   from ``GET /api/spotify/status``.
2. Clicks "Re-authoriser" → browser hits ``GET /spotify/auth/start`` which
   redirects to Spotify's authorize URL with a CSRF state cookie.
3. Spotify redirects back to ``GET /spotify/auth/callback`` which exchanges
   the code for a token, writes it to the spotipy file cache, and bounces
   the user back to the dashboard.

The Spotify app must list ``{webui.baseUrl}/spotify/auth/callback`` as an
allowed redirect URI, and ``spotify.spotifyRedirectUri`` in config must match.
"""

import html
import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.core import logging as logutil
from src.core.config import config_store
from src.integrations.spotify import (
    build_oauth,
    fetch_account_summary,
    get_token_status,
    sp,
)
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.spotify")

STATE_COOKIE = "spotify_oauth_state"
CALLBACK_PATH = "/spotify/auth/callback"


def _callback_url() -> str:
    """Build the OAuth callback URL from ``webui.baseUrl``.

    Single source of truth: avoids drift between the URL we send to Spotify
    in ``/start`` and the one we declare in ``/callback`` for the token
    exchange — both must match, and both must match what's registered in the
    Spotify developer dashboard.
    """
    base = config_store.get().get("config", {}).get("webui", {}).get("baseUrl", "")
    return f"{base.rstrip('/')}{CALLBACK_PATH}"


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/spotify/status")
    async def api_spotify_status(request: Request):
        """Report whether the Spotify token cache is populated and valid."""
        ctx.require_developer(request)
        status = get_token_status()
        status["callback_url"] = _callback_url()
        if status.get("authorized"):
            status["account"] = fetch_account_summary()
        return JSONResponse(status)

    @router.get("/spotify/auth/start")
    async def spotify_auth_start(request: Request):
        """Begin Spotify OAuth: redirect the admin to Spotify's authorize page."""
        ctx.require_developer(request)
        redirect_uri = _callback_url()
        if not redirect_uri.endswith(CALLBACK_PATH) or redirect_uri == CALLBACK_PATH:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Configure d'abord webui.baseUrl (section « Dashboard Web ») "
                    "pour que le callback OAuth pointe vers le dashboard."
                ),
            )
        try:
            oauth = build_oauth(redirect_uri=redirect_uri)
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Configuration Spotify invalide : {e}"
            ) from e
        if not oauth.client_id or not oauth.client_secret:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Client ID et Client Secret doivent être renseignés dans la "
                    "section Spotify de la configuration globale."
                ),
            )

        state = secrets.token_urlsafe(24)
        auth_url = oauth.get_authorize_url(state=state)
        response = RedirectResponse(url=auth_url)
        response.set_cookie(STATE_COOKIE, state, httponly=True, max_age=600, samesite="lax")
        return response

    @router.get("/spotify/auth/callback")
    async def spotify_auth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        """Receive Spotify's redirect and exchange the code for a token."""
        ctx.require_developer(request)

        if error:
            return _result_page(ok=False, message=f"Spotify a refusé l'autorisation : {error}")

        expected_state = request.cookies.get(STATE_COOKIE)
        if not expected_state or expected_state != state:
            return _result_page(
                ok=False,
                message=("État OAuth invalide ou expiré. Relance le flux depuis le dashboard."),
            )
        if not code:
            return _result_page(ok=False, message="Code d'autorisation manquant.")

        try:
            oauth = build_oauth(redirect_uri=_callback_url())
            oauth.get_access_token(code=code, as_dict=False, check_cache=False)
        except Exception as e:
            logger.error("Spotify token exchange failed: %s", e)
            return _result_page(
                ok=False,
                message="Échec de l'échange du code. Voir les logs du bot pour le détail.",
            )

        sp.reset()
        account = fetch_account_summary()
        logger.info(
            "Spotify re-authorized via WebUI (account=%s)",
            account.get("id") if account else "?",
        )

        response = _result_page(
            ok=True,
            message=(
                f"Connecté en tant que {account['display_name']} ({account['id']})."
                if account
                else "Token enregistré."
            ),
        )
        response.delete_cookie(STATE_COOKIE)
        return response

    return router


def _result_page(*, ok: bool, message: str) -> HTMLResponse:
    """Tiny self-closing page that bounces back to the dashboard."""
    title = "Spotify connecté" if ok else "Échec de connexion Spotify"
    color = "#1ed760" if ok else "#e53e3e"
    safe_message_html = html.escape(message)
    # URL-encode first, then HTML-escape for safe embedding inside an href attribute.
    safe_message_url = html.escape(quote(message, safe=" .,:;()@/-_"), quote=True)
    status = "ok" if ok else "error"
    body = f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8"><title>{title}</title>
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
<h1>{title}</h1>
<p>{safe_message_html}</p>
<a href="/#/global?section=spotify&spotify_auth={status}&msg={safe_message_url}">Retour au dashboard</a>
</div></body></html>"""
    return HTMLResponse(content=body, status_code=200 if ok else 400)
