"""Per-server configuration, channel listing, and EmbedManager publish."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from features.customcommands import MODULE_KEY as CUSTOM_COMMANDS_MODULE
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.embedmanager import MODULE_KEY as EMBED_MANAGER_MODULE
from src.discord_ext.embedmanager import build_embeds, publish_embeds
from src.webui.botops import run_extension_op, sync_commands
from src.webui.channels import list_channels
from src.webui.context import WebUIContext
from src.webui.secrets import mask_server_config, restore_section

logger = logutil.init_logger("webui.routes.servers")


class ModuleToggle(BaseModel):
    module: str
    enabled: bool


class ConfigUpdate(BaseModel):
    config: dict


# Ceiling on one module's saved config (serialized). Every guild shares
# config/config.json, so one guild must not be able to bloat it unboundedly.
MAX_MODULE_CONFIG_BYTES = 256 * 1024


def _require_known_module(module_name: str) -> None:
    """404 unless *module_name* is a registered per-guild module.

    Without this, ``servers.<id>.<anything>`` is writable — including keys that
    are not module configs at all, such as the guild's public dashboard token.
    A module's schema is registered when its extension is imported, so the
    module of an extension that isn't loaded is unknown too: it does nothing
    until the extension is enabled.
    """
    from src.webui import schemas

    if schemas.module_model(module_name) is None:
        raise HTTPException(status_code=404, detail=f"Module inconnu : {module_name}")


def _require_valid(errors: list[str]) -> None:
    """422 listing what the schema rejected, if anything."""
    if errors:
        raise HTTPException(
            status_code=422, detail="Configuration invalide : " + " ; ".join(errors[:10])
        )


def _require_reasonable_size(config: dict) -> None:
    size = len(json.dumps(config, ensure_ascii=False).encode("utf-8"))
    if size > MAX_MODULE_CONFIG_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Configuration trop volumineuse ({size // 1024} Ko, "
                f"maximum {MAX_MODULE_CONFIG_BYTES // 1024} Ko)."
            ),
        )


async def _try_reload_extension_for_module(
    ctx: WebUIContext, module_name: str, *, enabled_changed: bool = True
) -> dict:
    """Auto-reload the extensions that read ``module_name`` after a config change.

    The reload runs on the bot loop (see ``src.webui.botops``) — doing it inline
    on the Web UI loop mutates the client from the wrong thread and leaves the
    library's command sync stranded on a loop that owns none of the bot's
    connections.

    When the module was switched on or off in a guild (*enabled_changed*),
    every loaded extension declaring it in its ``MODULE_KEYS`` is reloaded
    (``moduleUtils`` backs admin, polls and reminders): their commands are
    scoped to the guilds enabled at import. A save that leaves ``enabled`` as
    it was only reloads the extensions declaring ``RELOAD_ON_SAVE``, which
    take their config when they load; the others read it live.

    Never reloaded: the extensions disabled in the extension list. The
    library *loads* an extension it is asked to reload, so a module save
    racing an unload would otherwise switch a disabled extension back on.
    A module no loaded extension reads (such as ``discord2name``, read on
    demand) has nothing to reload.

    No command sync here; the callers sync their guild when the module's
    command scopes may have changed (see :func:`_sync_guild_commands`).

    Returns ``{"reloaded": str|None, "error": str|None, "skipped": bool}``;
    ``reloaded`` names the reloaded extensions, comma-separated.
    """
    if not ctx.bot or not ctx.bot_loop_alive():
        return {"reloaded": None, "error": "Bot non disponible", "skipped": False}
    ext_paths = ctx.module_extension_map(on_save=not enabled_changed).get(module_name)
    if not ext_paths:
        return {"reloaded": None, "error": None, "skipped": True}
    # Same default as main.py: an extension missing from the list is enabled.
    ext_enabled = ctx.get_full_config().get("config", {}).get("extensions", {})
    ext_paths = [path for path in ext_paths if ext_enabled.get(path, True)]
    if not ext_paths:
        return {"reloaded": None, "error": None, "skipped": True}

    reloaded: list[str] = []
    failures: list[str] = []
    for ext_path in ext_paths:
        try:
            await run_extension_op(ctx, "reload", ext_path)
        except Exception as e:
            logger.error(f"Auto-reload failed for {ext_path}: {e}")
            failures.append(f"{ext_path} ({type(e).__name__})")
        else:
            logger.info(f"Auto-reloaded {ext_path} after config change for {module_name}")
            reloaded.append(ext_path)
    return {
        "reloaded": ", ".join(reloaded) or None,
        "error": (
            f"Échec du rechargement : {', '.join(failures)}. Voir les logs." if failures else None
        ),
        "skipped": False,
    }


def _reload_succeeded(reload_result: dict) -> bool:
    """Every extension reading the module came back, so syncing loses nothing.

    After a failed reload the client may be missing that extension's commands,
    and syncing would delete them from Discord.
    """
    return bool(reload_result["reloaded"]) and not reload_result["error"]


def _module_enabled(module_config: object) -> bool:
    """The test ``ModuleConfig`` applies when listing a module's enabled guilds."""
    return isinstance(module_config, dict) and bool(module_config.get("enabled", False))


async def _sync_guild_commands(ctx: WebUIContext, server_id: str) -> dict:
    """Push this guild's slash commands to Discord after a module reload.

    Module commands are scoped to the guilds where the module is enabled, and
    custom commands are built from the guild's config: the reload re-registers
    them on the client, but Discord only learns about the new set — commands
    added *or* removed — once the guild's scope is synced.

    Returns ``{"synced": bool, "error": str|None}``.
    """
    if not ctx.bot or not ctx.bot_loop_alive():
        return {"synced": False, "error": "Bot non disponible"}
    try:
        await sync_commands(ctx, scopes=[int(server_id)])
    except TimeoutError:
        logger.error("Command sync timed out for server %s", server_id)
        return {"synced": False, "error": "Synchronisation des commandes trop longue"}
    except Exception as e:
        logger.error("Command sync failed for server %s: %s", server_id, e)
        return {
            "synced": False,
            "error": f"Échec de la synchronisation ({type(e).__name__}). Voir les logs.",
        }
    logger.info("Synced slash commands for server %s", server_id)
    return {"synced": True, "error": None}


async def _apply_module_change(
    ctx: WebUIContext, server_id: str, module_name: str, *, scopes_changed: bool, sync: bool
) -> dict:
    """Reload what reads *module_name*, then sync the guild's commands if asked.

    The sync only follows a reload that brought every extension back: after a
    failed one the client may lack that extension's commands, and syncing
    would delete them from Discord. Returns the response's ``reload`` (and
    ``commandSync``) entries.
    """
    reload_result = await _try_reload_extension_for_module(
        ctx, module_name, enabled_changed=scopes_changed
    )
    out: dict = {"reload": reload_result}
    if sync and _reload_succeeded(reload_result):
        out["commandSync"] = await _sync_guild_commands(ctx, server_id)
    return out


async def _visible_servers(ctx: WebUIContext, session) -> dict:
    """The guilds *session* may see in the dashboard, with their masked config.

    Developers see every guild the bot is in; anyone else the guilds they
    manage now (read live, so a member demoted since login stops seeing one).
    Only guilds the bot is in are listed.
    """
    data = ctx.get_full_config()
    servers = data.get("servers", {})

    # Bot guild cache: authoritative names/icons even for guilds the
    # user doesn't manage (OAuth only describes the user's own guilds).
    bot_guilds: dict[str, object] = {}
    if ctx.bot and ctx.bot.guilds:
        bot_guilds = {str(g.id): g for g in ctx.bot.guilds}
    bot_guild_ids = set(bot_guilds)

    is_developer = ctx.oauth.is_developer(session)
    # Login-time OAuth snapshot: names/icons, and the guilds worth checking.
    oauth_guilds = {str(g.get("id")): g for g in session.guilds if isinstance(g, dict)}
    if ctx.bot and not is_developer:
        # Who manages what is read live (one cached check per shared guild),
        # so a member demoted since login stops seeing that guild's config.
        candidates = sorted(gid for gid in oauth_guilds if gid in bot_guild_ids)
        allowed = await asyncio.gather(
            *(ctx.user_manages_guild(session, gid) for gid in candidates),
            return_exceptions=True,
        )
        managed_guilds = {
            gid: oauth_guilds[gid]
            for gid, ok in zip(candidates, allowed, strict=True)
            if ok is True
        }
    else:
        managed_guilds = {g["id"]: g for g in ctx.oauth.get_user_managed_guilds(session)}

    def server_entry(server_id: str, server_config: dict) -> dict:
        guild_info = managed_guilds.get(server_id) or oauth_guilds.get(server_id, {})
        bot_guild = bot_guilds.get(server_id)
        name = (
            guild_info.get("name")
            or getattr(bot_guild, "name", None)
            or server_config.get("serverName")
            or f"Serveur {server_id}"
        )
        icon = guild_info.get("icon") or getattr(getattr(bot_guild, "icon", None), "hash", None)
        return {"name": name, "icon": icon, "config": mask_server_config(server_config)}

    result: dict = {}

    # Servers already in config — only if bot is in them and the user
    # manages them (a guild admin must not see other guilds' config).
    for server_id, server_config in servers.items():
        if bot_guild_ids and str(server_id) not in bot_guild_ids:
            continue
        if not is_developer and str(server_id) not in managed_guilds:
            continue
        result[server_id] = server_entry(str(server_id), server_config)

    # Guilds not yet in config — the user's managed guilds, plus every
    # bot guild for developers. Only if the bot is in them.
    candidate_ids = set(managed_guilds)
    if is_developer:
        candidate_ids |= bot_guild_ids
    for guild_id in candidate_ids:
        if guild_id not in result and (not bot_guild_ids or guild_id in bot_guild_ids):
            result[guild_id] = server_entry(guild_id, {})
    return result


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/servers")
    async def api_get_servers(request: Request):
        """Get servers the user manages (developers see every bot guild)."""
        session = ctx.require_admin(request)
        return JSONResponse(await _visible_servers(ctx, session))

    @router.get("/api/servers/{server_id}/channels")
    async def api_get_server_channels(request: Request, server_id: str):
        """List text/news channels for the given server (for channel-picker dropdowns)."""
        await ctx.require_guild_admin(request, server_id)
        ctx.require_bot_loop()

        data = await ctx.call_bot(
            list_channels, ctx.bot, server_id, what=f"list channels of guild {server_id}"
        )
        return JSONResponse(data)

    @router.get("/api/servers/{server_id}")
    async def api_get_server(request: Request, server_id: str):
        """Get configuration for a specific server (secrets masked)."""
        await ctx.require_guild_admin(request, server_id)
        data = ctx.get_full_config()
        server_config = data.get("servers", {}).get(server_id)
        if server_config is None:
            raise HTTPException(status_code=404, detail="Serveur non trouvé")
        return JSONResponse({"server_id": server_id, "config": mask_server_config(server_config)})

    @router.put("/api/servers/{server_id}/modules/{module_name}")
    async def api_update_module(
        request: Request, server_id: str, module_name: str, body: ConfigUpdate
    ):
        """Update a specific module's config for a server.

        Secret fields submitted as the untouched mask placeholder keep their
        current on-disk value.
        """
        session = await ctx.require_guild_admin(request, server_id)
        _require_known_module(module_name)
        _require_reasonable_size(body.config)
        from src.webui import schemas

        schema = schemas.MODULE_SCHEMAS.get(module_name)
        model = schemas.module_model(module_name)
        enabled_change: dict[str, bool] = {}

        def mutator(data: dict) -> None:
            server = data.setdefault("servers", {}).setdefault(server_id, {})
            enabled_change["before"] = _module_enabled(server.get(module_name))
            restored = restore_section(body.config, server.get(module_name), schema)
            if model is not None:
                # Raising here leaves config.json untouched (mutate writes after).
                _require_valid(schemas.validation_errors(model, restored, module=True))
            server[module_name] = restored
            enabled_change["after"] = _module_enabled(server[module_name])

        ctx.mutate_config(mutator)
        logger.info(
            "Updated %s config for server %s (by %s/%s)",
            module_name,
            server_id,
            session.username,
            session.user_id,
        )
        # A save only changes the guild's commands when it flips the module's
        # enabled state (the commands follow it) or edits custom commands.
        scopes_changed = enabled_change["before"] != enabled_change["after"]
        after = await _apply_module_change(
            ctx,
            server_id,
            module_name,
            scopes_changed=scopes_changed,
            sync=scopes_changed or module_name == CUSTOM_COMMANDS_MODULE,
        )
        return JSONResponse({"status": "ok", **after})

    @router.post("/api/servers/{server_id}/modules/{module_name}/toggle")
    async def api_toggle_module(
        request: Request, server_id: str, module_name: str, body: ModuleToggle
    ):
        """Enable or disable a module for a server."""
        session = await ctx.require_guild_admin(request, server_id)
        _require_known_module(module_name)

        def mutator(data: dict) -> None:
            server = data.setdefault("servers", {}).setdefault(server_id, {})
            # Hand-edited configs may hold a non-dict value here; replace it
            # rather than crashing on the item assignment below.
            if not isinstance(server.get(module_name), dict):
                server[module_name] = {}
            server[module_name]["enabled"] = body.enabled

        ctx.mutate_config(mutator)
        logger.info(
            "%s %s for server %s (by %s/%s)",
            "Enabled" if body.enabled else "Disabled",
            module_name,
            server_id,
            session.username,
            session.user_id,
        )
        # The reload re-scopes the module's commands to its new set of guilds;
        # syncing this guild makes them appear or disappear without a restart.
        after = await _apply_module_change(
            ctx, server_id, module_name, scopes_changed=True, sync=True
        )
        return JSONResponse({"status": "ok", "enabled": body.enabled, **after})

    @router.post(f"/api/servers/{{server_id}}/modules/{EMBED_MANAGER_MODULE}/publish")
    async def api_embedmanager_publish(request: Request, server_id: str):
        """Publish configured embeds to the target Discord message."""
        await ctx.require_guild_admin(request, server_id)
        ctx.require_bot_loop()

        guild_config = ModuleConfig(EMBED_MANAGER_MODULE).get(server_id)
        if not guild_config:
            raise HTTPException(status_code=400, detail="Module non activé sur ce serveur")

        if not guild_config.get("channelId"):
            raise HTTPException(status_code=400, detail="Salon de publication non configuré")
        embeds_config = guild_config.get("embeds") or []
        if not embeds_config:
            raise HTTPException(status_code=400, detail="Aucun embed configuré")
        embed_count = len(build_embeds(embeds_config))
        if not embed_count:
            raise HTTPException(status_code=500, detail="Erreur lors de la génération des embeds")

        async def _publish():
            if not await publish_embeds(ctx.bot, server_id, guild_config, log=logger):
                raise HTTPException(
                    status_code=502, detail="Impossible de créer ou récupérer le message cible"
                )

        await ctx.call_bot(
            _publish, what=f"publish EmbedManager embeds for guild {server_id}", timeout=60.0
        )

        return JSONResponse({"status": "ok", "count": embed_count})

    return router
