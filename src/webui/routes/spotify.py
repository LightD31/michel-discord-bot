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

import secrets
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.core import logging as logutil
from src.integrations.spotify import (
    build_oauth,
    fetch_account_summary,
    get_token_status,
    sp,
)
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.spotify")

STATE_COOKIE = "spotify_oauth_state"


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/spotify/status")
    async def api_spotify_status(request: Request):
        """Report whether the Spotify token cache is populated and valid."""
        ctx.require_admin(request)
        status = get_token_status()
        if status.get("authorized"):
            status["account"] = fetch_account_summary()
        return JSONResponse(status)

    @router.get("/spotify/auth/start")
    async def spotify_auth_start(request: Request):
        """Begin Spotify OAuth: redirect the admin to Spotify's authorize page."""
        ctx.require_admin(request)
        try:
            oauth = build_oauth()
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Configuration Spotify invalide : {e}"
            ) from e
        if not oauth.client_id or not oauth.client_secret or not oauth.redirect_uri:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Client ID, Client Secret et Redirect URI doivent être renseignés "
                    "dans la section Spotify de la configuration globale."
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
        ctx.require_admin(request)

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
            oauth = build_oauth()
            oauth.get_access_token(code=code, as_dict=False, check_cache=False)
        except Exception as e:
            logger.error("Spotify token exchange failed: %s", e)
            return _result_page(ok=False, message=f"Échec de l'échange du code : {e}")

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
    safe_message = quote(message, safe=" .,:;()@/-_")
    html = f"""<!doctype html>
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
<p>{message}</p>
<a href="/#/global?section=spotify&spotify_auth={"ok" if ok else "error"}&msg={safe_message}">Retour au dashboard</a>
</div></body></html>"""
    return HTMLResponse(content=html, status_code=200 if ok else 400)
