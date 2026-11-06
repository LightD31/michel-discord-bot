"""Reaction-role menu CRUD endpoints.

The WebUI builder calls these to create / edit / delete role menus on a guild.
Each mutation persists in MongoDB **and** posts/edits/deletes the actual
Discord message via the bot client, so the menu stays in sync with what
``/rolemenu`` would produce.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from features.reactionroles import (
    MenuAuthor,
    ReactionRolesRepository,
    RoleMenu,
    RoleMenuEntry,
    role_menu_refusal,
)
from src.core import logging as logutil
from src.discord_ext.messages import edit_message_if_changed
from src.discord_ext.rolemenus import (
    MAX_ENTRIES,
    bot_top_position,
    build_components,
    build_embed,
    entries_refusal,
    menu_author,
    role_info,
)
from src.webui.auth import Session
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


async def _resolve_author(ctx: WebUIContext, guild, session: Session) -> MenuAuthor:
    """Who is editing the menu, read live from the guild (bot loop only).

    Feeds the role-menu rules (``features/reactionroles/permissions.py``): the
    dashboard lets ``MANAGE_GUILD`` in, but handing out a role takes
    ``MANAGE_ROLES`` and a higher role, exactly as in Discord.
    """
    is_developer = ctx.oauth.is_developer(session)
    member = await guild.fetch_member(int(session.user_id))
    if member is None and not is_developer:
        raise HTTPException(status_code=403, detail="Vous n'êtes pas membre de ce serveur")
    return menu_author(guild, member, bypass_hierarchy=is_developer)


async def _check_entries(ctx: WebUIContext, guild, session: Session, entries) -> None:
    """Refuse (400) any entry the editing member could not hand out themselves."""
    author = await _resolve_author(ctx, guild, session)
    refusal = entries_refusal(
        guild, entries, author=author, bot_position=await bot_top_position(ctx.bot, guild)
    )
    if refusal:
        raise HTTPException(status_code=400, detail=refusal)


# ── Router factory ──────────────────────────────────────────────────


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/roles")
    async def api_list_roles(request: Request, server_id: str):
        """List the roles the requester may put in a menu (for the role-picker)."""
        session = await ctx.require_guild_admin(request, server_id)

        async def _fetch():
            try:
                guild = await ctx.bot.fetch_guild(int(server_id))
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Serveur introuvable: {e}") from e
            if guild is None:
                raise HTTPException(status_code=404, detail="Serveur introuvable")
            author = await _resolve_author(ctx, guild, session)
            bot_position = await bot_top_position(ctx.bot, guild)
            roles = []
            for r in getattr(guild, "roles", []) or []:
                # Same rules as a save: skips @everyone, integration roles,
                # staff roles and anything above the requester or the bot.
                if role_menu_refusal(
                    role_info(r, guild.id), author=author, bot_top_position=bot_position
                ):
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

        data = await ctx.call_bot(_fetch, what=f"list roles of guild {server_id}")
        return JSONResponse(data)

    @router.get("/api/servers/{server_id}/rolemenus")
    async def api_list_rolemenus(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        repo = ReactionRolesRepository(server_id)
        try:
            menus = await repo.list()
        except Exception as e:
            logger.exception("List rolemenus failed for %s: %s", server_id, e)
            raise HTTPException(
                status_code=500, detail="Erreur interne — détails dans les logs du bot."
            ) from e
        return JSONResponse({"menus": [_menu_to_dict(m) for m in menus]})

    @router.post("/api/servers/{server_id}/rolemenus")
    async def api_create_rolemenu(request: Request, server_id: str, body: RoleMenuCreate):
        session = await ctx.require_guild_admin(request, server_id)

        async def _create():
            guild, channel = await _resolve_guild_and_channel(ctx.bot, server_id, body.channel_id)
            guild_role_ids = {str(r.id) for r in getattr(guild, "roles", []) or []}
            parsed = _validate_entries(body.entries, guild_role_ids)
            if isinstance(parsed, str):
                raise HTTPException(status_code=400, detail=parsed)
            await _check_entries(ctx, guild, session, parsed)

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

        menu = await ctx.call_bot(_create, what=f"create a role menu in guild {server_id}")
        logger.info("Rolemenu created via WebUI for guild %s", server_id)
        return JSONResponse({"status": "ok", "menu": _menu_to_dict(menu)})

    @router.put("/api/servers/{server_id}/rolemenus/{menu_id}")
    async def api_update_rolemenu(
        request: Request, server_id: str, menu_id: str, body: RoleMenuUpdate
    ):
        session = await ctx.require_guild_admin(request, server_id)

        async def _update():
            repo = ReactionRolesRepository(server_id)
            existing = await repo.get(menu_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Menu introuvable")

            try:
                guild = await ctx.bot.fetch_guild(int(server_id))
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Serveur introuvable: {e}") from e
            if guild is None:
                raise HTTPException(status_code=404, detail="Serveur introuvable")
            guild_role_ids = {str(r.id) for r in getattr(guild, "roles", []) or []}

            new_title = body.title if body.title is not None else existing.title
            new_description = (
                body.description if body.description is not None else existing.description
            )
            if body.entries is not None:
                parsed = _validate_entries(body.entries, guild_role_ids)
                if isinstance(parsed, str):
                    raise HTTPException(status_code=400, detail=parsed)
                await _check_entries(ctx, guild, session, parsed)
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
                            await edit_message_if_changed(
                                msg, embeds=[embed], components=components, logger=logger
                            )
                except Exception as e:
                    logger.warning("Could not edit role menu message %s: %s", menu_id, e)

            updated = await repo.get(menu_id)
            return updated

        menu = await ctx.call_bot(_update, what=f"update role menu {menu_id} in guild {server_id}")
        logger.info("Rolemenu %s updated via WebUI for guild %s", menu_id, server_id)
        return JSONResponse({"status": "ok", "menu": _menu_to_dict(menu) if menu else None})

    @router.delete("/api/servers/{server_id}/rolemenus/{menu_id}")
    async def api_delete_rolemenu(request: Request, server_id: str, menu_id: str):
        await ctx.require_guild_admin(request, server_id)

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

        await ctx.call_bot(_delete, what=f"delete role menu {menu_id} in guild {server_id}")
        logger.info("Rolemenu %s deleted via WebUI for guild %s", menu_id, server_id)
        return JSONResponse({"status": "ok"})

    return router
