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

``GET /api/servers/{id}/spotify/stats`` feeds the charts on a guild's Spotify
module page: the playlist mirror and vote archive, aggregated by
:func:`features.spotify.aggregate`. Read-only, MongoDB only — no Spotify API
call and nothing dispatched to the bot loop.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from features.spotify import PlaylistStats, SpotifyRepository, aggregate
from src.core import logging as logutil
from src.core.logging import log_failure
from src.integrations.spotify import (
    build_oauth,
    fetch_account_summary,
    get_token_status,
    reset_client,
    run_spotify,
)
from src.webui import audit
from src.webui.context import WebUIContext
from src.webui.oauth_flow import (
    callback_url,
    error_word,
    redirect_with_state,
    result_page,
    state_matches,
)

logger = logutil.init_logger("webui.routes.spotify")

STATE_COOKIE = "spotify_oauth_state"
CALLBACK_PATH = "/spotify/auth/callback"


def _callback_url() -> str:
    """The OAuth callback, from ``webui.baseUrl``: what Spotify's app must list."""
    return callback_url(CALLBACK_PATH)


def _result_page(*, ok: bool, message: str) -> HTMLResponse:
    return result_page(
        service="Spotify", ok=ok, message=message, back="section=spotify&spotify_auth"
    )


async def collect_spotify_stats(server_id: str) -> PlaylistStats:
    """Playlist and vote statistics for *server_id*, straight from MongoDB.

    Shared by the admin module page and the public dashboard
    (``routes/public.py``). Raises ``DatabaseError`` when MongoDB fails.
    """
    repo = SpotifyRepository(server_id)
    playlist = await repo.find_playlist_items({})
    votes = await repo.find_vote_docs({})
    vote_infos = await repo.get_vote_infos() or {}
    return aggregate(playlist.values(), votes.values(), vote_infos.get("track_id"))


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/spotify/status")
    async def api_spotify_status(request: Request):
        """Report whether the Spotify token cache is populated and valid."""
        ctx.require_developer(request)
        status = get_token_status()
        status["callback_url"] = _callback_url()
        if status.get("authorized"):
            status["account"] = await fetch_account_summary()
        return JSONResponse(status)

    @router.get("/api/servers/{server_id}/spotify/stats")
    async def api_spotify_stats(request: Request, server_id: str):
        """Playlist and vote statistics for the guild's Spotify dashboard charts."""
        await ctx.require_guild_admin(request, server_id)
        stats = await collect_spotify_stats(server_id)
        return JSONResponse(stats.to_dict())

    @router.get("/spotify/auth/start")
    async def spotify_auth_start(request: Request):
        """Begin Spotify OAuth: redirect the admin to Spotify's authorize page."""
        ctx.require_developer(request)
        redirect_uri = _callback_url()
        try:
            oauth = build_oauth(redirect_uri=redirect_uri)
        except Exception as e:
            log_failure(logger, "Spotify OAuth setup failed: %s", e)
            raise HTTPException(
                status_code=500,
                detail="Configuration Spotify invalide (détail dans les logs du bot).",
            ) from e
        if not oauth.client_id or not oauth.client_secret:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Client ID et Client Secret doivent être renseignés dans la "
                    "section Spotify de la configuration globale."
                ),
            )
        return redirect_with_state(
            ctx, request, lambda state: oauth.get_authorize_url(state=state), STATE_COOKIE
        )

    @router.get("/spotify/auth/callback")
    async def spotify_auth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        """Receive Spotify's redirect and exchange the code for a token."""
        session = ctx.require_developer(request)

        if error:
            # Spotify's error codes are short snake_case words (access_denied);
            # anything else in the query string isn't echoed onto our page.
            return _result_page(
                ok=False, message=f"Spotify a refusé l'autorisation ({error_word(error)})."
            )

        if not state_matches(request, STATE_COOKIE, state):
            return _result_page(
                ok=False,
                message=("État OAuth invalide ou expiré. Relance le flux depuis le dashboard."),
            )
        if not code:
            return _result_page(ok=False, message="Code d'autorisation manquant.")

        try:
            oauth = build_oauth(redirect_uri=_callback_url())
            # Blocking HTTP + token cache write: on the Spotify worker, never the loop.
            await run_spotify(oauth.get_access_token, code=code, as_dict=False, check_cache=False)
        except Exception as e:
            log_failure(logger, "Spotify token exchange failed: %s", e)
            return _result_page(
                ok=False,
                message="Échec de l'échange du code. Voir les logs du bot pour le détail.",
            )

        reset_client()
        account = await fetch_account_summary()
        logger.info(
            "Spotify re-authorized via WebUI (account=%s, by %s/%s)",
            account.get("id") if account else "?",
            session.username,
            session.user_id,
        )
        await audit.record(None, session, "spotify.authorize")

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
