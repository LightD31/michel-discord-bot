"""Per-server configuration, channels/members listing, and EmbedManager publish."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src import logutil
from src.core.config import load_config as bot_load_config
from src.webui.context import WebUIContext, build_module_to_extension_map

logger = logutil.init_logger("webui.routes.servers")


class ModuleToggle(BaseModel):
    module: str
    enabled: bool


class ConfigUpdate(BaseModel):
    config: dict


def _try_reload_extension_for_module(ctx: WebUIContext, module_name: str) -> dict:
    """Auto-reload the extension that owns ``module_name`` after a config save.

    Returns ``{"reloaded": str|None, "error": str|None, "skipped": bool}``.
    """
    SKIP_MODULES = {"discord2name", "moduleEmbedManager"}
    if module_name in SKIP_MODULES:
        return {"reloaded": None, "error": None, "skipped": True}
    if not ctx.bot:
        return {"reloaded": None, "error": "Bot non disponible", "skipped": False}
    mapping = build_module_to_extension_map()
    ext_path = mapping.get(module_name)
    if not ext_path:
        return {
            "reloaded": None,
            "error": f"Aucune extension trouvée pour {module_name}",
            "skipped": False,
        }
    try:
        ctx.bot.reload_extension(ext_path)
        logger.info(f"Auto-reloaded {ext_path} after config change for {module_name}")
        return {"reloaded": ext_path, "error": None, "skipped": False}
    except Exception as e:
        logger.error(f"Auto-reload failed for {ext_path}: {e}")
        return {"reloaded": None, "error": str(e), "skipped": False}


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers")
    async def api_get_servers(request: Request):
        """Get servers where both the user and the bot are present."""
        session = ctx.require_admin(request)
        data = ctx.get_full_config()
        servers = data.get("servers", {})

        bot_guild_ids: set[str] = set()
        if ctx.bot and ctx.bot.guilds:
            bot_guild_ids = {str(g.id) for g in ctx.bot.guilds}

        user_guilds = {g["id"]: g for g in session.guilds}
        result: dict = {}

        # Servers already in config — only if bot is in them
        for server_id, server_config in servers.items():
            if bot_guild_ids and str(server_id) not in bot_guild_ids:
                continue
            guild_info = user_guilds.get(str(server_id), {})
            result[server_id] = {
                "name": guild_info.get(
                    "name", server_config.get("serverName", f"Serveur {server_id}")
                ),
                "icon": guild_info.get("icon"),
                "config": server_config,
            }

        # User's managed guilds not yet in config — only if bot is in them
        for guild_id, guild_info in user_guilds.items():
            if guild_id not in result and (not bot_guild_ids or guild_id in bot_guild_ids):
                result[guild_id] = {
                    "name": guild_info.get("name", f"Serveur {guild_id}"),
                    "icon": guild_info.get("icon"),
                    "config": {},
                }

        return JSONResponse(result)

    @router.get("/api/servers/{server_id}/channels")
    async def api_get_server_channels(request: Request, server_id: str):
        """List text/news channels for the given server (for channel-picker dropdowns)."""
        ctx.require_admin(request)
        if not ctx.bot or not ctx.bot_loop:
            raise HTTPException(status_code=503, detail="Bot non disponible")

        async def _fetch():
            from interactions import ChannelType

            try:
                guild = await ctx.bot.fetch_guild(int(server_id))
            except Exception as e:
                raise HTTPException(status_code=404, detail=f"Serveur introuvable: {e}") from e
            if guild is None:
                raise HTTPException(status_code=404, detail="Serveur introuvable")
            try:
                channels = await guild.fetch_channels()
            except Exception:
                channels = getattr(guild, "channels", []) or []
            text_channel_types = {
                ChannelType.GUILD_TEXT,
                ChannelType.GUILD_NEWS,
            }
            thread_channel_types = {
                ChannelType.GUILD_NEWS_THREAD,
                ChannelType.GUILD_PUBLIC_THREAD,
                ChannelType.GUILD_PRIVATE_THREAD,
            }
            result = []
            for c in channels:
                try:
                    ctype = getattr(c, "type", None)
                    if ctype not in text_channel_types and ctype not in thread_channel_types:
                        continue
                    parent_id = None
                    parent = getattr(c, "parent_id", None) or getattr(c, "category_id", None)
                    if parent:
                        parent_id = str(parent)
                    result.append(
                        {
                            "id": str(c.id),
                            "name": getattr(c, "name", str(c.id)),
                            "parent_id": parent_id,
                            "position": getattr(c, "position", 0) or 0,
                            "is_thread": ctype in thread_channel_types,
                            "archived": bool(getattr(c, "archived", False)),
                        }
                    )
                except Exception:
                    continue
            # Active threads (including private ones the bot can see)
            try:
                thread_list = await guild.fetch_active_threads()
                active_threads = getattr(thread_list, "threads", None) or []
            except Exception as e:
                logger.debug(f"Could not fetch active threads for {server_id}: {e}")
                active_threads = []
            known_ids = {c["id"] for c in result}
            for t in active_threads:
                try:
                    tid = str(t.id)
                    if tid in known_ids:
                        continue
                    parent_id = getattr(t, "parent_id", None)
                    result.append(
                        {
                            "id": tid,
                            "name": getattr(t, "name", tid),
                            "parent_id": str(parent_id) if parent_id else None,
                            "position": 0,
                            "is_thread": True,
                            "archived": bool(getattr(t, "archived", False)),
                        }
                    )
                    known_ids.add(tid)
                except Exception:
                    continue
            categories = []
            for c in channels:
                if getattr(c, "type", None) == ChannelType.GUILD_CATEGORY:
                    categories.append(
                        {
                            "id": str(c.id),
                            "name": getattr(c, "name", str(c.id)),
                            "position": getattr(c, "position", 0) or 0,
                        }
                    )
            result.sort(key=lambda x: (x["position"], x["name"]))
            categories.sort(key=lambda x: x["position"])
            return {"channels": result, "categories": categories}

        try:
            future = asyncio.run_coroutine_threadsafe(_fetch(), ctx.bot_loop)
            data = await asyncio.wrap_future(future)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to list channels for {server_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e
        return JSONResponse(data)

    @router.get("/api/servers/{server_id}/members")
    async def api_get_server_members(request: Request, server_id: str):
        """List members of the given server (for member-picker dropdowns).

        Pulls from the ``users`` MongoDB collection maintained by userinfoext;
        falls back to the live guild member cache if the collection is empty.
        """
        ctx.require_admin(request)
        if not ctx.bot or not ctx.bot_loop:
            raise HTTPException(status_code=503, detail="Bot non disponible")

        async def _fetch():
            from src.core.db import mongo_manager

            members: list[dict] = []
            seen: set[str] = set()

            # Primary source: userinfoext users collection
            try:
                collection = mongo_manager.get_guild_collection(server_id, "users")
                cursor = collection.find(
                    {},
                    {"_id": 1, "username": 1, "display_name": 1},
                )
                async for doc in cursor:
                    uid = str(doc.get("_id", ""))
                    if not uid or uid in seen:
                        continue
                    seen.add(uid)
                    username = doc.get("username") or ""
                    display = doc.get("display_name") or username or uid
                    members.append(
                        {
                            "id": uid,
                            "username": username,
                            "display_name": display,
                        }
                    )
            except Exception as e:
                logger.debug(f"Could not read users collection for {server_id}: {e}")

            # Fallback / supplement: live guild member cache
            try:
                guild = await ctx.bot.fetch_guild(int(server_id))
            except Exception:
                guild = None
            if guild is not None:
                for m in getattr(guild, "members", []) or []:
                    try:
                        uid = str(m.id)
                        if uid in seen:
                            continue
                        if getattr(m, "bot", False):
                            continue
                        seen.add(uid)
                        username = getattr(m, "username", "") or ""
                        display = getattr(m, "display_name", None) or username or uid
                        members.append(
                            {
                                "id": uid,
                                "username": username,
                                "display_name": display,
                            }
                        )
                    except Exception:
                        continue

            members.sort(key=lambda x: (x.get("display_name") or x.get("username") or "").lower())
            return {"members": members}

        try:
            future = asyncio.run_coroutine_threadsafe(_fetch(), ctx.bot_loop)
            data = await asyncio.wrap_future(future)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to list members for {server_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e
        return JSONResponse(data)

    @router.get("/api/servers/{server_id}")
    async def api_get_server(request: Request, server_id: str):
        """Get configuration for a specific server."""
        ctx.require_admin(request)
        data = ctx.get_full_config()
        server_config = data.get("servers", {}).get(server_id)
        if server_config is None:
            raise HTTPException(status_code=404, detail="Serveur non trouvé")
        return JSONResponse({"server_id": server_id, "config": server_config})

    @router.put("/api/servers/{server_id}/modules/{module_name}")
    async def api_update_module(
        request: Request, server_id: str, module_name: str, body: ConfigUpdate
    ):
        """Update a specific module's config for a server."""
        ctx.require_admin(request)
        data = ctx.get_full_config()

        if server_id not in data.get("servers", {}):
            data.setdefault("servers", {})[server_id] = {}

        data["servers"][server_id][module_name] = body.config
        ctx.save_config(data)
        logger.info(f"Updated {module_name} config for server {server_id}")
        reload_result = _try_reload_extension_for_module(ctx, module_name)
        return JSONResponse({"status": "ok", "reload": reload_result})

    @router.post("/api/servers/{server_id}/modules/{module_name}/toggle")
    async def api_toggle_module(
        request: Request, server_id: str, module_name: str, body: ModuleToggle
    ):
        """Enable or disable a module for a server."""
        ctx.require_admin(request)
        data = ctx.get_full_config()

        if server_id not in data.get("servers", {}):
            data.setdefault("servers", {})[server_id] = {}

        if module_name not in data["servers"][server_id]:
            data["servers"][server_id][module_name] = {}

        data["servers"][server_id][module_name]["enabled"] = body.enabled
        ctx.save_config(data)
        logger.info(
            f"{'Enabled' if body.enabled else 'Disabled'} {module_name} for server {server_id}"
        )
        reload_result = _try_reload_extension_for_module(ctx, module_name)
        return JSONResponse({"status": "ok", "enabled": body.enabled, "reload": reload_result})

    @router.post("/api/servers/{server_id}/modules/moduleEmbedManager/publish")
    async def api_embedmanager_publish(request: Request, server_id: str):
        """Publish configured embeds to the target Discord message."""
        ctx.require_admin(request)
        if not ctx.bot or not ctx.bot_loop:
            raise HTTPException(status_code=503, detail="Bot non disponible")

        _, module_config, enabled_servers = bot_load_config("moduleEmbedManager")
        if server_id not in enabled_servers:
            raise HTTPException(status_code=400, detail="Module non activé sur ce serveur")

        guild_config = module_config.get(server_id, {})
        channel_id = guild_config.get("channelId")
        message_id = guild_config.get("messageId")
        pin_message = bool(guild_config.get("pinMessage", False))
        embeds_config = guild_config.get("embeds", [])

        if not channel_id:
            raise HTTPException(status_code=400, detail="Salon de publication non configuré")
        if not embeds_config:
            raise HTTPException(status_code=400, detail="Aucun embed configuré")

        from extensions.embedmanager import build_embeds

        discord_embeds = build_embeds(embeds_config)
        if not discord_embeds:
            raise HTTPException(status_code=500, detail="Erreur lors de la génération des embeds")

        try:

            async def _publish():
                from src.discord_ext.messages import fetch_or_create_persistent_message

                message = await fetch_or_create_persistent_message(
                    ctx.bot,
                    channel_id=channel_id,
                    message_id=message_id,
                    module_name="moduleEmbedManager",
                    message_id_key="messageId",
                    guild_id=server_id,
                    initial_content="Initialisation…",
                    pin=pin_message,
                    logger=logger,
                )
                if message is None:
                    raise RuntimeError("Impossible de créer ou récupérer le message cible")
                await message.edit(content="", embeds=discord_embeds)

            future = asyncio.run_coroutine_threadsafe(_publish(), ctx.bot_loop)
            await asyncio.wrap_future(future)
        except Exception as e:
            logger.error(f"EmbedManager publish failed for server {server_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e

        return JSONResponse({"status": "ok", "count": len(discord_embeds)})

    return router
