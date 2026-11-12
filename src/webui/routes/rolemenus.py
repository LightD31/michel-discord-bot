"""Reaction-role menu CRUD endpoints.

The WebUI builder calls these to create / edit / delete role menus on a guild.
Each mutation persists in MongoDB **and** posts/edits/deletes the actual
Discord message via the bot client, so the menu stays in sync with what
``/rolemenu`` would produce.
"""

from typing import Any

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
from src.core import clock
from src.core import logging as logutil
from src.discord_ext.channels import fetch_guild_channel
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


async def _fetch_guild(bot, guild_id: str):
    """The guild, or a 404 (the reason goes to the logs, never into the response)."""
    try:
        guild = await bot.fetch_guild(int(guild_id))
    except Exception as e:
        logger.warning("Guild %s unavailable: %s", guild_id, e)
        raise HTTPException(status_code=404, detail="Serveur introuvable") from e
    if guild is None:
        raise HTTPException(status_code=404, detail="Serveur introuvable")
    return guild


async def _resolve_guild_and_channel(bot, guild_id: str, channel_id: str):
    guild = await _fetch_guild(bot, guild_id)
    # Another guild's channel is refused like an unknown one: the bot would
    # otherwise post the menu there with its own permissions.
    channel = await fetch_guild_channel(bot, guild_id, channel_id, logger=logger)
    if channel is None:
        raise HTTPException(status_code=400, detail="Salon introuvable sur ce serveur")
    if not hasattr(channel, "send"):
        raise HTTPException(status_code=400, detail="Salon invalide ou non accessible")
    return guild, channel


async def _menu_message(bot, menu: RoleMenu):
    """The Discord message of *menu*, ``None`` when it has none or it is gone."""
    if not menu.message_id:
        return None
    channel = await bot.fetch_channel(int(menu.channel_id))
    if not channel or not hasattr(channel, "fetch_message"):
        return None
    return await channel.fetch_message(int(menu.message_id))


def _role_color(role) -> str | None:
    color = getattr(role, "color", None)
    try:
        return f"#{int(color):06x}" if color is not None else None
    except (TypeError, ValueError):
        return None


def _parse_entries(entries_in: list[RoleMenuEntryIn], guild) -> list[RoleMenuEntry]:
    """The validated entries, or a 400 saying which one is wrong."""
    guild_role_ids = {str(r.id) for r in getattr(guild, "roles", []) or []}
    parsed = _validate_entries(entries_in, guild_role_ids)
    if isinstance(parsed, str):
        raise HTTPException(status_code=400, detail=parsed)
    return parsed


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


# ── Operations (run on the bot loop) ────────────────────────────────


async def _role_choices(ctx: WebUIContext, server_id: str, session: Session) -> dict:
    """The roles the requester may put in a menu, highest first."""
    guild = await _fetch_guild(ctx.bot, server_id)
    author = await _resolve_author(ctx, guild, session)
    bot_position = await bot_top_position(ctx.bot, guild)
    roles: list[dict[str, Any]] = [
        {
            "id": str(r.id),
            "name": getattr(r, "name", str(r.id)),
            "position": getattr(r, "position", 0) or 0,
            "color": _role_color(r),
        }
        for r in getattr(guild, "roles", []) or []
        # Same rules as a save: skips @everyone, integration roles, staff
        # roles and anything above the requester or the bot.
        if not role_menu_refusal(
            role_info(r, guild.id), author=author, bot_top_position=bot_position
        )
    ]
    roles.sort(key=lambda x: -x["position"])
    return {"roles": roles}


async def _create_menu(
    ctx: WebUIContext, server_id: str, session: Session, body: RoleMenuCreate
) -> RoleMenu:
    """Store the menu, then post it; a failed post removes it again."""
    guild, channel = await _resolve_guild_and_channel(ctx.bot, server_id, body.channel_id)
    entries = _parse_entries(body.entries, guild)
    await _check_entries(ctx, guild, session, entries)

    repo = ReactionRolesRepository(server_id)
    menu = RoleMenu(
        guild_id=str(server_id),
        channel_id=str(channel.id),
        title=body.title,
        description=body.description,
        entries=entries,
        created_by=str(getattr(session, "user_id", "webui")),
        created_at=clock.now(),
    )
    menu_id = await repo.add(menu)
    try:
        sent = await channel.send(
            embeds=[build_embed(body.title, body.description, entries)],
            components=build_components(menu_id, entries),
        )
    except Exception as e:
        await repo.delete(menu_id)
        logger.warning("Posting role menu %s in guild %s failed: %s", menu_id, server_id, e)
        raise HTTPException(status_code=502, detail="Échec de l'envoi du menu dans Discord") from e
    await repo.update(menu_id, message_id=str(sent.id))
    menu.id = menu_id
    menu.message_id = str(sent.id)
    return menu


async def _update_menu(
    ctx: WebUIContext, server_id: str, menu_id: str, session: Session, body: RoleMenuUpdate
) -> RoleMenu | None:
    """Save the new title, description or entries, then edit the posted menu."""
    repo = ReactionRolesRepository(server_id)
    existing = await repo.get(menu_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Menu introuvable")
    guild = await _fetch_guild(ctx.bot, server_id)

    title = body.title if body.title is not None else existing.title
    description = body.description if body.description is not None else existing.description
    entries = existing.entries
    if body.entries is not None:
        entries = _parse_entries(body.entries, guild)
        await _check_entries(ctx, guild, session, entries)

    await repo.update(
        menu_id,
        title=title,
        description=description,
        entries=[e.model_dump() for e in entries],
    )
    try:
        message = await _menu_message(ctx.bot, existing)
        if message:
            await edit_message_if_changed(
                message,
                embeds=[build_embed(title, description, entries)],
                components=build_components(menu_id, entries),
                logger=logger,
            )
    except Exception as e:  # noqa: BLE001 — saved; the message catches up at the next edit
        logger.warning("Could not edit role menu message %s: %s", menu_id, e)
    return await repo.get(menu_id)


async def _delete_menu(ctx: WebUIContext, server_id: str, menu_id: str) -> None:
    """Forget the menu, then delete its message if it is still there."""
    repo = ReactionRolesRepository(server_id)
    existing = await repo.get(menu_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Menu introuvable")
    await repo.delete(menu_id)
    try:
        message = await _menu_message(ctx.bot, existing)
        if message:
            await message.delete()
    except Exception as e:  # noqa: BLE001 — the menu is gone either way
        logger.warning("Could not delete role menu message %s: %s", menu_id, e)


# ── Router factory ──────────────────────────────────────────────────


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers/{server_id}/roles")
    async def api_list_roles(request: Request, server_id: str):
        """List the roles the requester may put in a menu (for the role-picker)."""
        session = await ctx.require_guild_admin(request, server_id)
        data = await ctx.call_bot(
            _role_choices, ctx, server_id, session, what=f"list roles of guild {server_id}"
        )
        return JSONResponse(data)

    @router.get("/api/servers/{server_id}/rolemenus")
    async def api_list_rolemenus(request: Request, server_id: str):
        await ctx.require_guild_admin(request, server_id)
        try:
            menus = await ReactionRolesRepository(server_id).list()
        except Exception as e:
            logger.exception("List rolemenus failed for %s: %s", server_id, e)
            raise HTTPException(
                status_code=500, detail="Erreur interne — détails dans les logs du bot."
            ) from e
        return JSONResponse({"menus": [_menu_to_dict(m) for m in menus]})

    @router.post("/api/servers/{server_id}/rolemenus")
    async def api_create_rolemenu(request: Request, server_id: str, body: RoleMenuCreate):
        session = await ctx.require_guild_admin(request, server_id)
        menu = await ctx.call_bot(
            _create_menu,
            ctx,
            server_id,
            session,
            body,
            what=f"create a role menu in guild {server_id}",
        )
        logger.info(
            "Rolemenu created via WebUI for guild %s (by %s/%s)",
            server_id,
            session.username,
            session.user_id,
        )
        return JSONResponse({"status": "ok", "menu": _menu_to_dict(menu)})

    @router.put("/api/servers/{server_id}/rolemenus/{menu_id}")
    async def api_update_rolemenu(
        request: Request, server_id: str, menu_id: str, body: RoleMenuUpdate
    ):
        session = await ctx.require_guild_admin(request, server_id)
        menu = await ctx.call_bot(
            _update_menu,
            ctx,
            server_id,
            menu_id,
            session,
            body,
            what=f"update role menu {menu_id} in guild {server_id}",
        )
        logger.info(
            "Rolemenu %s updated via WebUI for guild %s (by %s/%s)",
            menu_id,
            server_id,
            session.username,
            session.user_id,
        )
        return JSONResponse({"status": "ok", "menu": _menu_to_dict(menu) if menu else None})

    @router.delete("/api/servers/{server_id}/rolemenus/{menu_id}")
    async def api_delete_rolemenu(request: Request, server_id: str, menu_id: str):
        session = await ctx.require_guild_admin(request, server_id)
        await ctx.call_bot(
            _delete_menu,
            ctx,
            server_id,
            menu_id,
            what=f"delete role menu {menu_id} in guild {server_id}",
        )
        logger.info(
            "Rolemenu %s deleted via WebUI for guild %s (by %s/%s)",
            menu_id,
            server_id,
            session.username,
            session.user_id,
        )
        return JSONResponse({"status": "ok"})

    return router
