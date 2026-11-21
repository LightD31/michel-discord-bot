"""The dashboard's "Tester" buttons (C9): a feed read now, a connection tried now.

- "Tester ce flux" (``POST …/rss/test``): the feed's latest entries, each
  rendered as the bot would post it in text mode (``features.rss.render``),
  with the label and template of the form, saved or not. The URL is someone
  else's: it goes through ``fetch_untrusted()`` like every poll (public
  addresses only, body capped).
- "Vérifier la connexion" (``POST …/modules/{module}/check``): the module's
  *saved* settings tried against their service, so the stored secrets never
  leave the server. Only modules whose client lives outside their extension
  are listed in :data:`CHECKS` (a route never imports an extension).

Neither stores nor posts anything; neither is recorded in the audit log.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.rss.network import fetch_feed
from features.rss.render import render_text
from src.core import logging as logutil
from src.core.errors import IntegrationError
from src.core.http import BlockedUrlError
from src.core.urls import redact_url
from src.integrations.lldap import LldapClient, LldapError, LldapSettings
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.checks")

FEED_PREVIEW_ENTRIES = 3


class FeedTestBody(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    label: str = Field(default="", max_length=200)
    template: str = Field(default="", max_length=2000)


def entry_json(entry: Any, label: str, template: str) -> dict[str, Any]:
    feed_cfg = {"label": label, "template": template}
    return {
        "title": entry.title,
        "link": entry.link,
        "author": entry.author,
        "image": entry.image_url,
        "published": entry.published.isoformat() if entry.published else None,
        "summary": (entry.summary or "")[:300],
        "text": render_text(label or "Flux", feed_cfg, entry),
    }


async def check_lldap(settings: dict[str, Any]) -> str:
    """Log in to LLDAP with the saved account and list its groups."""
    lldap = LldapSettings.from_section(settings)
    if not lldap.configured:
        raise HTTPException(
            status_code=422,
            detail="Réglages incomplets : enregistre l'URL, le Base DN, le compte et son mot de passe.",
        )
    try:
        groups = await LldapClient().list_groups(lldap)
    except LldapError as e:
        # Our own French message, never a password (LldapError's contract).
        raise HTTPException(status_code=502, detail=f"❌ {e}") from e
    return f"Connexion à LLDAP réussie : {len(groups)} groupe(s) trouvé(s)."


# module → the check of its saved settings, which returns what to tell the admin.
CHECKS: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {
    "moduleLldap": check_lldap,
}


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/connection-checks")
    async def api_checks(request: Request):
        """The modules with a "Vérifier la connexion" button."""
        ctx.require_admin(request)
        return JSONResponse(sorted(CHECKS))

    @router.post("/api/servers/{server_id}/rss/test")
    async def api_rss_test(request: Request, server_id: str, body: FeedTestBody):
        session = await ctx.require_guild_admin(request, server_id)
        logger.info(
            "Feed test of %s for server %s (by %s/%s)",
            redact_url(body.url),
            server_id,
            session.username,
            session.user_id,
        )
        try:
            entries = await fetch_feed(body.url.strip())
        except BlockedUrlError as e:
            raise HTTPException(
                status_code=422,
                detail="Cette URL n'est pas autorisée : seules les adresses http(s) publiques le sont.",
            ) from e
        except IntegrationError as e:
            raise HTTPException(
                status_code=422,
                detail="Impossible de lire ce flux : il ne répond pas, ou ce n'est pas un flux RSS ou Atom.",
            ) from e
        if not entries:
            raise HTTPException(status_code=422, detail="Le flux est vide ou illisible.")
        latest = [entry_json(e, body.label, body.template) for e in entries[:FEED_PREVIEW_ENTRIES]]
        return JSONResponse({"count": len(entries), "entries": latest})

    @router.post("/api/servers/{server_id}/modules/{module_name}/check")
    async def api_check(request: Request, server_id: str, module_name: str):
        await ctx.require_guild_admin(request, server_id)
        check = CHECKS.get(module_name)
        if check is None:
            raise HTTPException(status_code=404, detail="Ce module ne se vérifie pas")
        server = ctx.get_full_config().get("servers", {}).get(server_id)
        settings = server.get(module_name) if isinstance(server, dict) else None
        if not isinstance(settings, dict) or not settings:
            raise HTTPException(
                status_code=422, detail="Enregistre d'abord les réglages du module."
            )
        return JSONResponse({"status": "ok", "message": await check(settings)})

    return router
