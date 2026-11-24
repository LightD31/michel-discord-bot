"""Copy a module's settings from another server the editor manages (P35).

``GET …/modules/{module}/copy-sources`` lists the servers to copy from: the
editor's other servers, managed right now, with settings for the module.
``POST …/modules/{module}/copy-from`` reads the source's settings on the
server, so a secret never reaches the browser, keeps the target's own
channels, roles, members and switch (:mod:`src.webui.module_copy`) and saves
through :func:`~src.webui.routes.servers.save_module_settings`, the path every
module save takes (checks, history, audit as ``module.copy``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.core.permissions import ADMINISTRATOR, has_any
from src.webui.context import WebUIContext
from src.webui.module_copy import copied_settings, holds_secrets
from src.webui.routes.servers import save_module_settings


class CopyBody(BaseModel):
    source: str = Field(min_length=1, max_length=25)


def _settings(ctx: WebUIContext, guild_id: str, module_name: str) -> dict[str, Any]:
    server = ctx.get_full_config().get("servers", {}).get(guild_id) or {}
    settings = server.get(module_name) if isinstance(server, dict) else None
    return settings if isinstance(settings, dict) else {}


def module_schema(module_name: str) -> dict[str, Any] | None:
    from src.webui import schemas

    return schemas.MODULE_SCHEMAS.get(module_name)


async def may_copy_secrets(ctx: WebUIContext, session: Any, guild_id: str) -> bool:
    """The source's owner or an administrator there, read live (as the Santa reveal)."""
    if not ctx.bot:
        return False
    access = await ctx.guild_access(session, guild_id)
    return bool(access.is_owner or has_any(access.permissions, ADMINISTRATOR))


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    def _known(module_name: str) -> dict[str, Any]:
        schema = module_schema(module_name)
        if schema is None:
            raise HTTPException(status_code=404, detail="Module inconnu")
        return schema

    @router.get("/api/servers/{server_id}/modules/{module_name}/copy-sources")
    async def api_copy_sources(request: Request, server_id: str, module_name: str):
        session = await ctx.require_guild_admin(request, server_id)
        schema = _known(module_name)
        sources = []
        for guild in session.guilds:
            gid = str(guild.get("id") or "")
            settings = _settings(ctx, gid, module_name) if gid != server_id else {}
            if not settings or not await ctx.user_manages_guild(session, gid):
                continue
            secrets = holds_secrets(schema, settings)
            sources.append(
                {
                    "id": gid,
                    "name": str(guild.get("name") or gid),
                    "secrets": secrets and await may_copy_secrets(ctx, session, gid),
                    "hasSecrets": secrets,
                }
            )
        return JSONResponse({"sources": sources})

    @router.post("/api/servers/{server_id}/modules/{module_name}/copy-from")
    async def api_copy_from(request: Request, server_id: str, module_name: str, body: CopyBody):
        session = await ctx.require_guild_admin(request, server_id)
        await ctx.require_guild_admin(request, body.source)
        schema = _known(module_name)
        if body.source == server_id:
            raise HTTPException(status_code=422, detail="Choisis un autre serveur que celui-ci.")
        source = _settings(ctx, body.source, module_name)
        if not source:
            raise HTTPException(
                status_code=404,
                detail="Ce module n'est pas réglé sur l'autre serveur : rien à copier.",
            )
        with_secrets = await may_copy_secrets(ctx, session, body.source)
        settings, to_fill = copied_settings(
            schema, source, _settings(ctx, server_id, module_name), with_secrets=with_secrets
        )
        source_name = next(
            (str(g.get("name") or "") for g in session.guilds if str(g.get("id")) == body.source),
            "",
        )
        after = await save_module_settings(
            ctx,
            server_id,
            module_name,
            settings,
            session,
            action="module.copy",
            details={"from": source_name or "un autre serveur"},
        )
        return JSONResponse(
            {
                "status": "ok",
                "toFill": to_fill,
                "secretsCopied": with_secrets and holds_secrets(schema, source),
                **after,
            }
        )

    return router


__all__ = ["create_router", "may_copy_secrets"]
