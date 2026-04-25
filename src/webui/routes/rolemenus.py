"""Reaction-role menu CRUD endpoints.

The WebUI builder calls these to create / edit / delete role menus on a guild.
Each mutation persists in MongoDB **and** posts/edits/deletes the actual
Discord message via the bot client, so the menu stays in sync with what
``/rolemenu`` would produce.
"""

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.reactionroles import ReactionRolesRepository, RoleMenu, RoleMenuEntry
from src.core import logging as logutil
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.rolemenus")


# ── Request models ──────────────────────────────────────────────────


class RoleMenuEntryIn(BaseModel):
    role_id: str
    emoji: str
    label: str


class RoleMenuCreate(BaseModel):
    channel_id: str
    title: str = Field(min_length=1, max_length=256)
    description: str | None = None
    entries: list[RoleMenuEntryIn]


class RoleMenuUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    entries: list[RoleMenuEntryIn] | None = None


# ── Helpers ─────────────────────────────────────────────────────────


def _menu_to_dict(menu: RoleMenu) -> dict:
    return {
        "id": menu.id,
        "guild_id": menu.guild_id,
        "channel_id": menu.channel_id,
        "message_id": menu.message_id,
        "title": menu.title,
        "description": menu.description,
        "entries": [e.model_dump() for e in menu.entries],
        "created_by": menu.created_by,
        "created_at": menu.created_at.isoformat() if menu.created_at else None,
    }


def _validate_entries(
    entries_in: list[RoleMenuEntryIn], guild_role_ids: set[str]
) -> list[RoleMenuEntry] | str:
    from extensions.reactionroles._common import MAX_ENTRIES

    if not entries_in:
        return "Au moins une entrée requise."
    if len(entries_in) > MAX_ENTRIES:
        return f"Maximum {MAX_ENTRIES} entrées par menu (limite Discord)."
    out: list[RoleMenuEntry] = []
    for idx, e in enumerate(entries_in, 1):
        if not e.role_id.isdigit():
            return f"Entrée {idx} : role_id doit être numérique."
        if e.role_id not in guild_role_ids:
            return f"Entrée {idx} : rôle <@&{e.role_id}> introuvable."
        if not e.emoji:
            return f"Entrée {idx} : emoji manquant."
        if not e.label.strip():
            return f"Entrée {idx} : libellé manquant."
        if len(e.label) > 80:
            return f"Entrée {idx} : libellé trop long (max 80)."
        out.append(RoleMenuEntry(role_id=e.role_id, emoji=e.emoji, label=e.label))
    return out


async def _resolve_guild_and_channel(bot, guild_id: str, channel_id: str):
    try:
        guild = await bot.fetch_guild(int(guild_id))
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Serveur introuvable: {e}") from e
    if guild is None:
        raise HTTPException(status_code=404, detail="Serveur introuvable")
    try:
        channel = await bot.fetch_channel(int(channel_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Salon introuvable: {e}") from e
    if channel is None or not hasattr(channel, "send"):
        raise HTTPException(status_code=400, detail="Salon invalide ou non accessible")
    return guild, channel


def _run_on_bot_loop(ctx: WebUIContext, coro):
    if not ctx.bot or not ctx.bot_loop:
        raise HTTPException(status_code=503, detail="Bot non disponible")
    future = asyncio.run_coroutine_threadsafe(coro, ctx.bot_loop)
    return asyncio.wrap_future(future)


# ── Router factory ──────────────────────────────────────────────────


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/roles")
    async def api_list_roles(request: Request, server_id: str):
        """List assignable roles in a guild (for the role-picker)."""
        ctx.require_admin(request)

        async def _fetch():
            try:
                guild = await ctx.bot.fetch_guild(int(server_id))
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Serveur introuvable: {e}") from e
            if guild is None:
                raise HTTPException(status_code=404, detail="Serveur introuvable")
            roles = []
            for r in getattr(guild, "roles", []) or []:
                # Skip @everyone (id == guild.id) and managed/integration roles.
                if str(r.id) == str(guild.id):
                    continue
                if getattr(r, "managed", False):
                    continue
                color = getattr(r, "color", None)
                color_hex = None
                if color is not None:
                    try:
                        color_hex = f"#{int(color):06x}"
                    except Exception:
                        color_hex = None
                roles.append(
                    {
                        "id": str(r.id),
                        "name": getattr(r, "name", str(r.id)),
                        "position": getattr(r, "position", 0) or 0,
                        "color": color_hex,
                    }
                )
            roles.sort(key=lambda x: -x["position"])  # higher position first
            return {"roles": roles}

        try:
            data = await _run_on_bot_loop(ctx, _fetch())
        except HTTPException:
            raise
        except Exception as e:
            logger.error("List roles failed for %s: %s", server_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        return JSONResponse(data)

    @router.get("/api/servers/{server_id}/rolemenus")
    async def api_list_rolemenus(request: Request, server_id: str):
        ctx.require_admin(request)
        repo = ReactionRolesRepository(server_id)
        try:
            menus = await repo.list()
        except Exception as e:
            logger.error("List rolemenus failed for %s: %s", server_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        return JSONResponse({"menus": [_menu_to_dict(m) for m in menus]})

    @router.post("/api/servers/{server_id}/rolemenus")
    async def api_create_rolemenu(request: Request, server_id: str, body: RoleMenuCreate):
        session = ctx.require_admin(request)

        async def _create():
            from extensions.reactionroles._common import build_components, build_embed

            guild, channel = await _resolve_guild_and_channel(ctx.bot, server_id, body.channel_id)
            guild_role_ids = {str(r.id) for r in getattr(guild, "roles", []) or []}
            parsed = _validate_entries(body.entries, guild_role_ids)
            if isinstance(parsed, str):
                raise HTTPException(status_code=400, detail=parsed)

            repo = ReactionRolesRepository(server_id)
            menu = RoleMenu(
                guild_id=str(server_id),
                channel_id=str(channel.id),
                title=body.title,
                description=body.description,
                entries=parsed,
                created_by=str(getattr(session, "user_id", "webui")),
                created_at=datetime.now(),
            )
            menu_id = await repo.add(menu)

            embed = build_embed(body.title, body.description, parsed)
            components = build_components(menu_id, parsed)
            try:
                sent = await channel.send(embeds=[embed], components=components)
            except Exception as e:
                await repo.delete(menu_id)
                raise HTTPException(
                    status_code=500, detail=f"Échec de l'envoi du menu : {e}"
                ) from e
            await repo.update(menu_id, message_id=str(sent.id))
            menu.id = menu_id
            menu.message_id = str(sent.id)
            return menu

        try:
            menu = await _run_on_bot_loop(ctx, _create())
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Create rolemenu failed for %s: %s", server_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        logger.info("Rolemenu created via WebUI for guild %s", server_id)
        return JSONResponse({"status": "ok", "menu": _menu_to_dict(menu)})

    @router.put("/api/servers/{server_id}/rolemenus/{menu_id}")
    async def api_update_rolemenu(
        request: Request, server_id: str, menu_id: str, body: RoleMenuUpdate
    ):
        ctx.require_admin(request)

        async def _update():
            from extensions.reactionroles._common import build_components, build_embed

            repo = ReactionRolesRepository(server_id)
            existing = await repo.get(menu_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Menu introuvable")

            try:
                guild = await ctx.bot.fetch_guild(int(server_id))
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Serveur introuvable: {e}") from e
            guild_role_ids = {str(r.id) for r in getattr(guild, "roles", []) or []}

            new_title = body.title if body.title is not None else existing.title
            new_description = (
                body.description if body.description is not None else existing.description
            )
            if body.entries is not None:
                parsed = _validate_entries(body.entries, guild_role_ids)
                if isinstance(parsed, str):
                    raise HTTPException(status_code=400, detail=parsed)
                new_entries = parsed
            else:
                new_entries = existing.entries

            await repo.update(
                menu_id,
                title=new_title,
                description=new_description,
                entries=[e.model_dump() for e in new_entries],
            )

            if existing.message_id:
                try:
                    channel = await ctx.bot.fetch_channel(int(existing.channel_id))
                    if channel and hasattr(channel, "fetch_message"):
                        msg = await channel.fetch_message(int(existing.message_id))
                        if msg:
                            embed = build_embed(new_title, new_description, new_entries)
                            components = build_components(menu_id, new_entries)
                            await msg.edit(embeds=[embed], components=components)
                except Exception as e:
                    logger.warning("Could not edit role menu message %s: %s", menu_id, e)

            updated = await repo.get(menu_id)
            return updated

        try:
            menu = await _run_on_bot_loop(ctx, _update())
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Update rolemenu failed for %s/%s: %s", server_id, menu_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        logger.info("Rolemenu %s updated via WebUI for guild %s", menu_id, server_id)
        return JSONResponse({"status": "ok", "menu": _menu_to_dict(menu) if menu else None})

    @router.delete("/api/servers/{server_id}/rolemenus/{menu_id}")
    async def api_delete_rolemenu(request: Request, server_id: str, menu_id: str):
        ctx.require_admin(request)

        async def _delete():
            repo = ReactionRolesRepository(server_id)
            existing = await repo.get(menu_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Menu introuvable")

            await repo.delete(menu_id)
            if existing.message_id:
                try:
                    channel = await ctx.bot.fetch_channel(int(existing.channel_id))
                    if channel and hasattr(channel, "fetch_message"):
                        msg = await channel.fetch_message(int(existing.message_id))
                        if msg:
                            await msg.delete()
                except Exception as e:
                    logger.warning("Could not delete role menu message %s: %s", menu_id, e)

        try:
            await _run_on_bot_loop(ctx, _delete())
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Delete rolemenu failed for %s/%s: %s", server_id, menu_id, e)
            raise HTTPException(status_code=500, detail=str(e)) from e
        logger.info("Rolemenu %s deleted via WebUI for guild %s", menu_id, server_id)
        return JSONResponse({"status": "ok"})

    return router
