"""Discord OAuth2 login / logout routes and ``/api/me``."""

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.core import logging as logutil
from src.webui.context import COOKIE_NAME, WebUIContext
from src.webui.ratelimit import RateLimiter, client_ip

logger = logutil.init_logger("webui.routes.auth")

# Per-IP budget shared by /auth/login and /auth/callback — generous for a
# human retrying a login, tight enough to throttle brute-forcing the flow.
AUTH_RATE_LIMIT = 10
AUTH_RATE_WINDOW_SECONDS = 60.0


def _is_https(request: Request) -> bool:
    """Whether the request reached us over HTTPS (honors X-Forwarded-Proto)."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if forwarded:
        return forwarded == "https"
    return request.url.scheme == "https"


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()
    auth_limiter = RateLimiter(AUTH_RATE_LIMIT, AUTH_RATE_WINDOW_SECONDS)

    def _throttle(request: Request) -> None:
        retry_after = auth_limiter.check(client_ip(request))
        if retry_after:
            logger.warning("Auth rate limit hit for %s", client_ip(request))
            raise HTTPException(
                status_code=429,
                detail="Trop de tentatives de connexion — réessaie dans un instant.",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    @router.get("/auth/login")
    async def auth_login(request: Request):
        """Redirect to Discord OAuth2 login."""
        _throttle(request)
        state = secrets.token_urlsafe(16)
        url = ctx.oauth.get_oauth_url(state)
        response = RedirectResponse(url=url)
        response.set_cookie(
            "oauth_state",
            state,
            httponly=True,
            max_age=300,
            samesite="lax",
            secure=_is_https(request),
        )
        return response

    @router.get("/auth/callback")
    async def auth_callback(request: Request, code: str = "", state: str = ""):
        """Handle Discord OAuth2 callback."""
        _throttle(request)
        if not code:
            raise HTTPException(status_code=400, detail="Code manquant")

        # CSRF check: the state we sent must come back and match the cookie.
        expected_state = request.cookies.get("oauth_state", "")
        if not state or not expected_state or not secrets.compare_digest(expected_state, state):
            rejection = HTMLResponse(
                content=(
                    "<h1>Connexion expirée</h1>"
                    "<p>État OAuth invalide ou expiré — relance la connexion.</p>"
                ),
                status_code=403,
            )
            rejection.delete_cookie("oauth_state")
            return rejection

        session = await ctx.oauth.exchange_code(code)
        if not session:
            return HTMLResponse(
                content="<h1>Accès refusé</h1><p>Vous n'êtes pas autorisé à accéder au dashboard.</p>",
                status_code=403,
            )

        response = RedirectResponse(url="/")
        response.set_cookie(
            COOKIE_NAME,
            session.session_token,
            httponly=True,
            max_age=86400,
            samesite="lax",
            secure=_is_https(request),
        )
        response.delete_cookie("oauth_state")
        return response

    @router.get("/auth/logout")
    async def auth_logout(request: Request):
        """Logout and invalidate the session."""
        token = request.cookies.get(COOKIE_NAME)
        if token:
            await ctx.oauth.invalidate_session(token)
        response = RedirectResponse(url="/")
        response.delete_cookie(COOKIE_NAME)
        return response

    @router.get("/api/me")
    async def api_me(request: Request):
        """Return current user info (and the guilds the bot shares with them)."""
        session = ctx.get_session(request)
        if not session:
            return JSONResponse({"authenticated": False})

        bot_guild_ids: set[str] = set()
        if ctx.bot and ctx.bot.guilds:
            bot_guild_ids = {str(g.id) for g in ctx.bot.guilds}

        managed = ctx.oauth.get_user_managed_guilds(session)
        guilds = [
            {
                "id": g["id"],
                "name": g["name"],
                "icon": g.get("icon"),
                "managed": True,
            }
            for g in managed
            if not bot_guild_ids or g["id"] in bot_guild_ids
        ]

        return JSONResponse(
            {
                "authenticated": True,
                "user_id": session.user_id,
                "username": session.username,
                "avatar": session.avatar,
                "guilds": guilds,
                "is_admin": ctx.is_admin_user(session),
                "is_developer": ctx.oauth.is_developer(session),
            }
        )

    return router
