"""Discord OAuth2 login / logout routes and ``/api/me``."""

import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.webui.context import COOKIE_NAME, WebUIContext


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/auth/login")
    async def auth_login():
        """Redirect to Discord OAuth2 login."""
        state = secrets.token_urlsafe(16)
        url = ctx.oauth.get_oauth_url(state)
        response = RedirectResponse(url=url)
        response.set_cookie("oauth_state", state, httponly=True, max_age=300)
        return response

    @router.get("/auth/callback")
    async def auth_callback(request: Request, code: str = "", state: str = ""):
        """Handle Discord OAuth2 callback."""
        if not code:
            raise HTTPException(status_code=400, detail="Code manquant")

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
        )
        response.delete_cookie("oauth_state")
        return response

    @router.get("/auth/logout")
    async def auth_logout(request: Request):
        """Logout and invalidate the session."""
        token = request.cookies.get(COOKIE_NAME)
        if token:
            ctx.oauth.invalidate_session(token)
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
