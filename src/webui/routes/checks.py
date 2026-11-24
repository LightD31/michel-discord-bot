"""The dashboard's "Tester" buttons: a feed read now, a connection tried now.

- "Tester ce flux" (``POST …/rss/test``): the feed's latest entries, each
  rendered as the bot would post it in text mode (``features.rss.render``),
  with the label and template of the form, saved or not. The URL is someone
  else's: it goes through ``fetch_untrusted()`` like every poll (public
  addresses only, body capped).
- "Vérifier la connexion" (``POST …/modules/{module}/check``): the module's
  *saved* settings tried against their service, so the stored secrets never
  leave the server. Only modules whose client lives outside their extension
  are listed in :data:`CHECKS` (a route never imports an extension): LLDAP,
  and since P16 Satisfactory, Minecraft, Uptime Kuma and YouTube, whose
  clients moved to ``src/integrations`` or ``features`` for it.

Neither stores nor posts anything; neither is recorded in the audit log.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.minecraft import MinecraftSettings
from features.rss.network import fetch_feed
from features.rss.render import render_text
from src.core import logging as logutil
from src.core.errors import IntegrationError
from src.core.http import BlockedUrlError
from src.core.urls import redact_url
from src.integrations import satisfactory, youtube
from src.integrations.lldap import LldapClient, LldapError, LldapSettings
from src.integrations.uptime_kuma import UptimeKumaError, UptimeKumaSettings, fetch_monitor_states
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


def _incomplete(what: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"Réglages incomplets : enregistre {what}.")


def _unreachable(what: str) -> HTTPException:
    return HTTPException(status_code=502, detail=f"❌ {what}")


async def check_satisfactory(settings: dict[str, Any]) -> str:
    """Reach the saved server through its pinned certificate and read its state (P16)."""
    connection = satisfactory.connection_of(settings)
    if connection is None:
        raise _incomplete("l'adresse du serveur et son jeton d'API")
    address, token, fingerprint = connection
    if fingerprint is None:
        raise _incomplete("l'empreinte SHA-256 du certificat (le jeton n'est envoyé qu'à elle)")
    try:
        api = await satisfactory.call(satisfactory.build_api, address, token, fingerprint)
        state = (await satisfactory.call(api.query_server_state))["serverGameState"]
    except Exception as e:  # noqa: BLE001 — what the admin needs is that it failed
        logger.info("Satisfactory check of %s failed: %s", address, e)
        raise _unreachable(
            "Le serveur ne répond pas, refuse le jeton, ou son certificat n'a pas cette empreinte."
        ) from e
    return (
        f"Serveur joignable : {state.get('numConnectedPlayers', 0)} joueur(s) connecté(s), "
        f"palier {state.get('techTier', '?')}."
    )


MINECRAFT_TIMEOUT_SECONDS = 10.0


async def check_minecraft(settings: dict[str, Any]) -> str:
    """Ask the saved Minecraft server for its status, as the status task does (P16)."""
    from mcstatus import JavaServer

    mc = MinecraftSettings.from_config(settings)
    if not mc.address and not mc.ip:
        raise _incomplete("l'adresse du serveur")

    def query():
        try:
            server = JavaServer.lookup(mc.address) if mc.address else JavaServer(mc.ip, mc.port)
        except Exception:  # noqa: BLE001 — the IP and port are the fallback, as in the task
            server = JavaServer(mc.ip, mc.port)
        return server.status()

    try:
        status = await asyncio.wait_for(asyncio.to_thread(query), MINECRAFT_TIMEOUT_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.info("Minecraft check of %s failed: %s", mc.address or mc.ip, e)
        raise _unreachable("Le serveur ne répond pas à cette adresse.") from e
    return (
        f"Serveur joignable : {status.players.online}/{status.players.max} joueur(s), "
        f"version {status.version.name}."
    )


async def check_uptime(settings: dict[str, Any]) -> str:
    """Read the server's Uptime Kuma monitors with its URL and key (P16, P37)."""
    kuma = UptimeKumaSettings.from_section(settings)
    if not kuma.metrics_configured:
        raise _incomplete("l'URL et la clé API d'Uptime Kuma")
    try:
        states = await fetch_monitor_states(kuma)
    except UptimeKumaError as e:
        logger.info("Uptime Kuma check failed: %s", e)
        raise _unreachable("Uptime Kuma ne répond pas ou refuse la clé d'API.") from e
    return f"Uptime Kuma répond : {len(states)} sonde(s) visible(s)."


YOUTUBE_CHECKED_CHANNELS = 5


async def check_youtube(settings: dict[str, Any]) -> str:
    """The API key, and the latest video of the first followed channels (P16)."""
    if not youtube.configured(settings):
        raise _incomplete("la clé et l'URL de l'API YouTube")
    handles = list(settings.get("youtubeChannelList") or {})[:YOUTUBE_CHECKED_CHANNELS]
    if not handles:
        raise _incomplete("au moins une chaîne à suivre")
    lines = []
    for handle in handles:
        try:
            latest = await youtube.latest_upload(
                settings, await youtube.uploads_playlist(settings, handle)
            )
            lines.append(f"{handle} : « {latest.get('title', '?')} »")
        except Exception as e:  # noqa: BLE001 — one channel wrong is said, the rest checked
            logger.info("YouTube check of %s failed: %s", handle, e)
            lines.append(f"{handle} : introuvable (le @ de la chaîne est-il juste ?)")
    return "Dernière vidéo de chaque chaîne : " + " · ".join(lines)


# module → the check of its saved settings, which returns what to tell the admin.
CHECKS: dict[str, Callable[[dict[str, Any]], Awaitable[str]]] = {
    "moduleLldap": check_lldap,
    "moduleMinecraft": check_minecraft,
    "moduleSatisfactory": check_satisfactory,
    "moduleUptime": check_uptime,
    "moduleYoutube": check_youtube,
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
