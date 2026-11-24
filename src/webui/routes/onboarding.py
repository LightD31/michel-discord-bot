"""A new server's first steps: the packs, the checklist, the announcement.

See :mod:`src.webui.onboarding`. Applying a pack goes through the module save
route's own checks (``routes/servers.py``), module by module, then reloads
what reads the new modules and syncs the guild's commands once.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.core import logging as logutil
from src.core.errors import DatabaseError
from src.webui import audit, schemas
from src.webui.audit import AuditRepository
from src.webui.context import WebUIContext
from src.webui.onboarding import (
    ANNOUNCE,
    DISMISSED_KEY,
    PACKS,
    TEMPLATE_TEST,
    WELCOME,
    announcement_text,
    checklist,
    missing_server_rights,
    pack_catalogue,
    pack_settings,
    post_announcement,
)
from src.webui.routes.servers import (
    reload_for_modules,
    reload_succeeded,
    require_allowed_additions,
    require_own_channels,
    require_valid,
    sync_guild_commands,
)

logger = logutil.init_logger("webui.routes.onboarding")


class PackBody(BaseModel):
    pack: str = Field(max_length=40)
    # module → field → what the wizard's picker chose
    answers: dict[str, dict[str, Any]] = Field(default_factory=dict)


class DismissBody(BaseModel):
    dismissed: bool = True


class AnnounceBody(BaseModel):
    channelId: str = Field(max_length=25)  # noqa: N815 (the SPA's spelling)


def _server(ctx: WebUIContext, server_id: str) -> dict[str, Any]:
    server = ctx.get_full_config().get("servers", {}).get(server_id)
    return server if isinstance(server, dict) else {}


async def _audit_facts(server_id: str) -> tuple[bool, bool]:
    """(welcome message tested, Michel announced), both ``False`` when MongoDB can't say."""
    repo = AuditRepository(server_id)
    try:
        return await repo.has(TEMPLATE_TEST, WELCOME), await repo.has(ANNOUNCE)
    except DatabaseError as e:
        logger.warning("Onboarding facts of %s unreadable: %s", server_id, e)
        return False, False


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/onboarding")
    async def api_onboarding(request: Request, server_id: str):
        """The checklist, the packs and the announcement's text, for the server page and wizard."""
        await ctx.require_guild_admin(request, server_id)
        server = _server(ctx, server_id)
        missing = None
        if ctx.bot is not None and ctx.bot_loop_alive():
            missing = await ctx.call_bot(
                _missing_rights, ctx.bot, server_id, what=f"read Michel's rights on {server_id}"
            )
        tested, announced = await _audit_facts(server_id)
        module_schemas = schemas.MODULE_SCHEMAS
        return JSONResponse(
            {
                "dismissed": server.get(DISMISSED_KEY) is True,
                "steps": checklist(
                    server, missing_rights=missing, welcome_tested=tested, announced=announced
                ),
                "packs": pack_catalogue(module_schemas),
                "announcement": announcement_text(server, module_schemas),
            }
        )

    @router.post("/api/servers/{server_id}/onboarding/pack")
    async def api_apply_pack(request: Request, server_id: str, body: PackBody):
        """Switch on a pack's modules the server has no settings for yet."""
        session = await ctx.require_guild_admin(request, server_id)
        pack = PACKS.get(body.pack)
        if pack is None:
            raise HTTPException(status_code=404, detail="Pack inconnu")
        writes, skipped = pack_settings(
            pack, _server(ctx, server_id), body.answers, schemas.MODULE_SCHEMAS
        )
        # The same checks as a save of each module from its page.
        for module, settings in writes.items():
            model = schemas.module_model(module)
            if model is not None:
                require_valid(schemas.validation_errors(model, settings, module=True))
            await require_own_channels(ctx, server_id, module, settings)
            await require_allowed_additions(ctx, server_id, module, settings, session)

        written: list[str] = []

        def mutator(data: dict) -> None:
            written.clear()
            server = data.setdefault("servers", {}).setdefault(server_id, {})
            for module, settings in writes.items():
                if module in server:
                    continue  # set in the meantime: never overwrite
                server[module] = settings
                written.append(module)

        ctx.mutate_config(mutator)
        logger.info(
            "Pack %s applied to server %s: %s (by %s/%s)",
            pack.key,
            server_id,
            written,
            session.username,
            session.user_id,
        )
        out: dict[str, Any] = {"status": "ok", "applied": written, "skipped": skipped}
        if written:
            out["reload"] = await reload_for_modules(ctx, written, enabled_changed=True)
            if reload_succeeded(out["reload"]):
                out["commandSync"] = await sync_guild_commands(ctx, server_id)
            await audit.record(
                server_id, session, "onboarding.pack", target=pack.label, changes=written
            )
        return JSONResponse(out)

    @router.post("/api/servers/{server_id}/onboarding/dismiss")
    async def api_dismiss(request: Request, server_id: str, body: DismissBody):
        """Hide the "Pour bien démarrer" list, or bring it back."""
        session = await ctx.require_guild_admin(request, server_id)

        def mutator(data: dict) -> None:
            server = data.setdefault("servers", {}).setdefault(server_id, {})
            if body.dismissed:
                server[DISMISSED_KEY] = True
            else:
                server.pop(DISMISSED_KEY, None)

        ctx.mutate_config(mutator)
        await audit.record(
            server_id, session, "onboarding.dismiss", details={"dismissed": body.dismissed}
        )
        return JSONResponse({"status": "ok", "dismissed": body.dismissed})

    @router.post("/api/servers/{server_id}/onboarding/announce")
    async def api_announce(request: Request, server_id: str, body: AnnounceBody):
        """Michel introduces itself in a channel the editor could write in."""
        session = await ctx.require_guild_admin(request, server_id)
        if not body.channelId.isdigit():
            raise HTTPException(status_code=422, detail="Choisis un salon.")
        ctx.require_bot_loop()
        text = announcement_text(_server(ctx, server_id), schemas.MODULE_SCHEMAS)
        url = await ctx.call_bot(
            post_announcement,
            ctx.bot,
            server_id,
            body.channelId,
            str(session.user_id),
            text,
            is_developer=ctx.oauth.is_developer(session),
            what=f"announce Michel in guild {server_id}",
        )
        logger.info(
            "Michel announced in %s/%s (by %s/%s)",
            server_id,
            body.channelId,
            session.username,
            session.user_id,
        )
        await audit.record(server_id, session, ANNOUNCE, target=None)
        return JSONResponse({"status": "ok", "url": url})

    return router


async def _missing_rights(bot: Any, server_id: str) -> list[str] | None:
    return missing_server_rights(bot, server_id)
