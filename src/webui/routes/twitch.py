"""The bot's Twitch account, connected from the global settings (P29).

1. A developer sees the state in the Twitch section (``GET /api/twitch/status``).
2. "🔌 Connecter Twitch" opens ``GET /twitch/auth/start``, which redirects to
   Twitch's consent page with a state cookie.
3. Twitch comes back to ``GET /twitch/auth/callback``: the code is exchanged,
   the tokens written where twitchAPI reads them (``data/twitchcreds.json``),
   and the extensions that use them (``twitch``, ``streamlabs``) reloaded on
   the bot loop, so EventSub starts without a restart.

The Twitch application must list ``{webui.baseUrl}/twitch/auth/callback`` as
an OAuth redirect URL.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.core import logging as logutil
from src.core.logging import log_failure
from src.integrations import twitch_auth
from src.webui import audit
from src.webui.botops import run_extension_op
from src.webui.context import WebUIContext
from src.webui.oauth_flow import (
    callback_url,
    error_word,
    redirect_with_state,
    result_page,
    state_matches,
)

logger = logutil.init_logger("webui.routes.twitch")

STATE_COOKIE = "twitch_oauth_state"
CALLBACK_PATH = "/twitch/auth/callback"
# The extensions that bind the stored token when they start.
USERS_OF_THE_TOKEN = ("extensions.twitch", "extensions.streamlabs")


def _result_page(*, ok: bool, message: str) -> HTMLResponse:
    return result_page(
        service="Twitch", ok=ok, message=message, back="global?section=twitch&twitch_auth"
    )


async def reload_token_users(ctx: WebUIContext) -> list[str]:
    """Reload the loaded extensions that use the token; returns those that failed."""
    loaded = set(ctx.get_extension_module_paths()) if ctx.bot else set()
    failed = []
    for ext in USERS_OF_THE_TOKEN:
        if ext not in loaded:
            continue
        try:
            await run_extension_op(ctx, "reload", ext)
        except Exception as e:
            log_failure(logger, "Reload of %s after the Twitch connection failed: %s", ext, e)
            failed.append(ext)
    return failed


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/twitch/status")
    async def api_twitch_status(request: Request):
        """Whether the bot's Twitch account is connected, and as whom."""
        ctx.require_developer(request)
        client_id, client_secret = twitch_auth.app_credentials()
        status: dict = {"app_configured": bool(client_id and client_secret)}
        try:
            status["callback_url"] = callback_url(CALLBACK_PATH)
        except HTTPException:
            status["callback_url"] = None
        try:
            status.update(await twitch_auth.account_status())
        except Exception as e:  # Twitch unreachable: say so, the page still shows
            log_failure(logger, "Twitch token check failed: %s", e)
            status.update(connected=twitch_auth.stored_tokens() is not None, unreachable=True)
        return JSONResponse(status)

    @router.get("/twitch/auth/start")
    async def twitch_auth_start(request: Request):
        """Send the developer to Twitch's consent page."""
        ctx.require_developer(request)
        redirect_uri = callback_url(CALLBACK_PATH)
        client_id, client_secret = twitch_auth.app_credentials()
        if not client_id or not client_secret:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Client ID et Client Secret doivent être renseignés dans la "
                    "section Twitch de la configuration globale."
                ),
            )
        return redirect_with_state(
            ctx,
            request,
            lambda state: twitch_auth.authorize_url(client_id, redirect_uri, state),
            STATE_COOKIE,
        )

    @router.get("/twitch/auth/callback")
    async def twitch_auth_callback(
        request: Request, code: str = "", state: str = "", error: str = ""
    ):
        """Receive Twitch's redirect, keep the tokens, restart what uses them."""
        session = ctx.require_developer(request)
        if error:
            return _result_page(
                ok=False, message=f"Twitch a refusé l'autorisation ({error_word(error)})."
            )
        if not state_matches(request, STATE_COOKIE, state):
            return _result_page(
                ok=False,
                message="État OAuth invalide ou expiré. Relance le flux depuis le dashboard.",
            )
        if not code:
            return _result_page(ok=False, message="Code d'autorisation manquant.")

        client_id, client_secret = twitch_auth.app_credentials()
        try:
            token, refresh = await twitch_auth.exchange_code(
                client_id, client_secret, code, callback_url(CALLBACK_PATH)
            )
            twitch_auth.save_tokens(token, refresh)
        except Exception as e:
            log_failure(logger, "Twitch token exchange failed: %s", e)
            return _result_page(
                ok=False,
                message="Échec de l'échange du code. Voir les logs du bot pour le détail.",
            )

        failed = await reload_token_users(ctx)
        logger.info("Twitch connected via WebUI (by %s/%s)", session.username, session.user_id)
        await audit.record(None, session, "twitch.connect")
        message = "Compte Twitch enregistré : les notifications de live redémarrent."
        if failed:
            message = (
                "Compte Twitch enregistré, mais le redémarrage de "
                f"{', '.join(ext.removeprefix('extensions.') for ext in failed)} a échoué : "
                "recharge les extensions depuis la page Extensions."
            )
        response = _result_page(ok=True, message=message)
        response.delete_cookie(STATE_COOKIE)
        return response

    return router


__all__ = ["CALLBACK_PATH", "STATE_COOKIE", "create_router", "reload_token_users"]
