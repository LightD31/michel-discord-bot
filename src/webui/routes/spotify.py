"""Spotify: each server connects its own account (P36), and its playlist statistics.

The flow, for a server's admins, from the Spotify module page:

1. The panel reads ``GET /api/servers/{id}/spotify/status``.
2. "Connecter Spotify" opens ``GET /spotify/auth/start?guild={id}``, which
   redirects to Spotify's authorize page with the server's own app. The state,
   kept in a cookie, starts with the guild id.
3. Spotify comes back to ``GET /spotify/auth/callback``. The state names the
   server; the cookie proves this browser started the flow; the admin check runs
   again. The code is exchanged and the token is written to
   ``data/spotify/<guild>.json``.

Each server's app must list ``{webui.baseUrl}/spotify/auth/callback`` as an
allowed redirect URI; the panel shows it.

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
    CALLBACK_PATH,
    build_oauth,
    fetch_account_summary,
    get_token_status,
    reset_client,
    run_spotify,
)
from src.webui import audit
from src.webui.context import WebUIContext
from src.webui.guild_access import is_guild_id
from src.webui.oauth_flow import (
    callback_url,
    error_word,
    redirect_with_state,
    result_page,
    state_matches,
)

logger = logutil.init_logger("webui.routes.spotify")

STATE_COOKIE = "spotify_oauth_state"


def _callback_url() -> str:
    """The OAuth callback, from ``webui.baseUrl``: what Spotify's app must list."""
    return callback_url(CALLBACK_PATH)


def _result_page(guild_id: str | None, *, ok: bool, message: str) -> HTMLResponse:
    page = f"module/{guild_id}/moduleSpotify?" if guild_id else "?"
    return result_page(service="Spotify", ok=ok, message=message, back=page + "spotify_auth")


def _state_guild(state: str) -> str | None:
    """The guild the flow's state names (``<guild>.<token>``), when it is one."""
    guild, _, token = state.partition(".")
    return guild if token and is_guild_id(guild) else None


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

    @router.get("/api/servers/{server_id}/spotify/status")
    async def api_spotify_status(request: Request, server_id: str):
        """The server's Spotify app and account, from its token file."""
        await ctx.require_guild_admin(request, server_id)
        status = get_token_status(server_id)
        status["callback_url"] = _callback_url()
        if status.get("authorized"):
            status["account"] = await fetch_account_summary(server_id)
        return JSONResponse(status)

    @router.get("/api/servers/{server_id}/spotify/stats")
    async def api_spotify_stats(request: Request, server_id: str):
        """Playlist and vote statistics for the guild's Spotify dashboard charts."""
        await ctx.require_guild_admin(request, server_id)
        stats = await collect_spotify_stats(server_id)
        return JSONResponse(stats.to_dict())

    @router.get("/spotify/auth/start")
    async def spotify_auth_start(request: Request, guild: str = ""):
        """Begin connecting *guild*'s account: redirect to Spotify's authorize page."""
        await ctx.require_guild_admin(request, guild)
        _callback_url()  # 400 while webui.baseUrl isn't set: Spotify couldn't come back
        oauth = build_oauth(guild)
        if not oauth.client_id or not oauth.client_secret:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Renseigne d'abord le Client ID et le Client Secret de l'application "
                    "Spotify dans les réglages du module, puis enregistre."
                ),
            )
        return redirect_with_state(
            ctx,
            request,
            lambda state: oauth.get_authorize_url(state=state),
            STATE_COOKIE,
            prefix=f"{guild}.",
        )

    @router.get("/spotify/auth/callback")
    async def spotify_auth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
    ):
        """Receive Spotify's redirect and exchange the code for the server's token."""
        guild = _state_guild(state)
        if error:
            # Spotify's error codes are short snake_case words (access_denied);
            # anything else in the query string isn't echoed onto our page.
            return _result_page(
                guild, ok=False, message=f"Spotify a refusé l'autorisation ({error_word(error)})."
            )
        if guild is None or not state_matches(request, STATE_COOKIE, state):
            return _result_page(
                guild,
                ok=False,
                message="État OAuth invalide ou expiré. Relance le flux depuis le dashboard.",
            )
        # Still an admin there: the flow may have started before a demotion.
        session = await ctx.require_guild_admin(request, guild)
        if not code:
            return _result_page(guild, ok=False, message="Code d'autorisation manquant.")

        try:
            oauth = build_oauth(guild)
            # Blocking HTTP + token file write: on the Spotify worker, never the loop.
            await run_spotify(oauth.get_access_token, code=code, as_dict=False, check_cache=False)
        except Exception as e:
            log_failure(logger, "Spotify token exchange failed for %s: %s", guild, e)
            return _result_page(
                guild,
                ok=False,
                message="Échec de l'échange du code. Voir les logs du bot pour le détail.",
            )

        reset_client(guild)
        account = await fetch_account_summary(guild)
        logger.info(
            "Spotify connected for %s (account=%s, by %s/%s)",
            guild,
            account.get("id") if account else "?",
            session.username,
            session.user_id,
        )
        await audit.record(
            guild,
            session,
            "spotify.authorize",
            details={"account": account.get("id")} if account else None,
        )

        response = _result_page(
            guild,
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
