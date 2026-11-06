"""Shared context + helpers for Web UI routers.

The FastAPI app factory builds a :class:`WebUIContext` from the bot handle,
event loop, and OAuth client, then passes it to each router's
``create_router(ctx)`` factory. Routers use the context to call bot
coroutines from the WebUI thread and to enforce auth (admin / developer).

Extracted from ``src/webui/app.py`` in Phase 5 so route definitions live in
focused modules (``routes/`` and ``sse/``) instead of one 1 000-line file.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request

from src.core import logging as logutil
from src.webui.auth import DiscordOAuth, Session
from src.webui.guild_access import GuildAccess, read_guild_access
from src.webui.ratelimit import TrustedProxies

logger = logutil.init_logger("webui.context")

COOKIE_NAME = "michel_session"

# How long a live permission check is reused. Bounds how long a demoted (or
# banned) member keeps managing a guild from the dashboard.
GUILD_ACCESS_TTL_SECONDS = 60.0
# Budget for reading one member from the bot (cache, else one API call).
GUILD_ACCESS_TIMEOUT_SECONDS = 10.0
# Budget for a route's Discord work on the bot loop (list channels, post a menu…).
BOT_CALL_TIMEOUT_SECONDS = 30.0


@dataclass
class WebUIContext:
    """Everything a route handler needs besides the request itself."""

    bot: Any
    bot_loop: asyncio.AbstractEventLoop | None
    oauth: DiscordOAuth
    # Which peers' X-Forwarded-* headers may be believed; see ratelimit.py.
    trusted_proxies: TrustedProxies = field(default_factory=TrustedProxies)
    # (guild_id, user_id) → (checked_at monotonic, access); see guild_access().
    _access_cache: dict[tuple[str, str], tuple[float, GuildAccess]] = field(
        default_factory=dict, repr=False
    )

    # --- Bot loop dispatch -------------------------------------------

    def bot_loop_alive(self) -> bool:
        """True if the bot's event loop is still running and accepting work."""
        return self.bot_loop is not None and not self.bot_loop.is_closed()

    def require_bot_loop(self) -> asyncio.AbstractEventLoop:
        """Return the bot loop or raise 503 if it's gone (no bot, or loop closed)."""
        if not self.bot or not self.bot_loop_alive():
            raise HTTPException(status_code=503, detail="Bot non disponible")
        assert self.bot_loop is not None
        return self.bot_loop

    async def run_on_bot_loop(self, func, *args, timeout: float, **kwargs) -> Any:
        """Run *func* on the bot's event loop and await its result from here.

        The Web UI serves requests on its own uvicorn loop in a daemon thread,
        so anything that touches the running client — the aiohttp session, the
        interaction registry, ``Task.start()`` — has to be dispatched onto the
        bot loop instead of being called inline. *func* may be a plain callable
        or return an awaitable; both are resolved on the bot loop.

        Raises ``HTTPException(503)`` if the bot loop is gone, ``TimeoutError``
        if the loop doesn't get to the call in time, and otherwise whatever
        *func* raises.
        """
        loop = self.require_bot_loop()

        async def _runner() -> Any:
            result = func(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

        future = asyncio.run_coroutine_threadsafe(_runner(), loop)
        return await asyncio.wait_for(asyncio.wrap_future(future), timeout=timeout)

    async def call_bot(
        self, func, *args, what: str, timeout: float = BOT_CALL_TIMEOUT_SECONDS, **kwargs
    ) -> Any:
        """:meth:`run_on_bot_loop` for route handlers, with HTTP-shaped failures.

        An ``HTTPException`` raised by *func* passes through; a bot that doesn't
        answer within *timeout* is a 504; anything else is logged with *what*
        and becomes a 500 whose message stays generic — exception text can
        carry internals (hosts, ids, driver errors) and belongs in the logs.
        """
        try:
            return await self.run_on_bot_loop(func, *args, timeout=timeout, **kwargs)
        except HTTPException:
            raise
        except TimeoutError as e:
            logger.error("Bot call timed out after %.0fs: %s", timeout, what)
            raise HTTPException(status_code=504, detail="Le bot ne répond pas — réessayez.") from e
        except Exception as e:
            logger.exception("Bot call failed: %s", what)
            raise HTTPException(
                status_code=500, detail="Erreur interne — détails dans les logs du bot."
            ) from e

    # --- Config I/O ---------------------------------------------------

    def get_full_config(self) -> dict:
        """Load the full config from disk; tolerate a missing file."""
        from src.core.config import load_full_config

        return load_full_config() or {"config": {}, "servers": {}}

    def save_config(self, data: dict) -> None:
        """Persist *data* atomically and notify ConfigStore subscribers.

        Raises an ``HTTPException`` carrying the underlying cause so dashboard
        saves surface an actionable message instead of a blind 500.
        """
        from src.core.config import config_store

        if not data.get("config") and not data.get("servers"):
            # An empty payload means the config file was unreadable when the
            # request loaded it — saving would wipe the real config.
            raise HTTPException(
                status_code=503,
                detail=(
                    "Configuration en mémoire vide (fichier illisible ?) — "
                    "sauvegarde refusée pour ne pas écraser config/config.json."
                ),
            )

        try:
            config_store.save_full(data)
        except Exception as e:
            self._raise_save_error(e)

    def mutate_config(self, mutator) -> dict:
        """Read-modify-write the config atomically (see ``ConfigStore.mutate``).

        Prefer this over ``get_full_config()`` + ``save_config()`` in routes:
        the mutator runs against a fresh read under the write lock, so a
        concurrent save can't be silently overwritten.
        """
        from src.core.config import config_store
        from src.core.errors import ConfigError

        try:
            return config_store.mutate(mutator)
        except ConfigError as e:
            raise HTTPException(
                status_code=503,
                detail=(
                    "config/config.json illisible ou vide — "
                    "modification refusée pour ne pas écraser la configuration."
                ),
            ) from e
        except HTTPException:
            raise
        except Exception as e:
            self._raise_save_error(e)
            raise  # unreachable — _raise_save_error always raises

    @staticmethod
    def _raise_save_error(e: Exception) -> None:
        logger.exception("Config save failed")
        detail = f"Échec d'écriture de config/config.json : {e}"
        if isinstance(e, PermissionError):
            detail += (
                " — le dossier config/ n'est pas inscriptible par l'utilisateur du "
                "conteneur (uid 1000). Sur l'hôte : chown -R 1000:1000 ./config"
            )
        raise HTTPException(status_code=500, detail=detail) from e

    # --- Session / authorization -------------------------------------

    def get_session(self, request: Request) -> Session | None:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        return self.oauth.get_session(token)

    def require_session(self, request: Request) -> Session:
        session = self.get_session(request)
        if not session:
            raise HTTPException(status_code=401, detail="Non authentifié")
        return session

    def is_admin_user(self, session: Session) -> bool:
        """Admin if user has MANAGE_GUILD/ADMINISTRATOR on any bot guild.

        A coarse gate from the login-time OAuth snapshot, for endpoints that
        expose nothing guild-specific (module list, schemas). Anything tied to
        one guild goes through :meth:`require_guild_admin`, which checks live.
        """
        if self.oauth.is_developer(session):
            return True
        if self.bot and self.bot.guilds:
            bot_guild_ids = {str(g.id) for g in self.bot.guilds}
            managed = self.oauth.get_user_managed_guilds(session)
            return any(g["id"] in bot_guild_ids for g in managed)
        return False

    def require_admin(self, request: Request) -> Session:
        session = self.require_session(request)
        if not self.is_admin_user(session):
            raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
        return session

    async def guild_access(self, session: Session, guild_id: str | int) -> GuildAccess:
        """The user's live standing in *guild_id*, cached for a minute.

        Read from the bot on its own loop (see :mod:`src.webui.guild_access`),
        never from the login-time OAuth snapshot. Raises ``HTTPException(503)``
        when the bot can't answer: authorization fails closed.
        """
        key = (str(guild_id), str(session.user_id))
        now = time.monotonic()
        cached = self._access_cache.get(key)
        if cached is not None and now - cached[0] < GUILD_ACCESS_TTL_SECONDS:
            return cached[1]
        try:
            access = await self.run_on_bot_loop(
                read_guild_access,
                self.bot,
                guild_id,
                session.user_id,
                timeout=GUILD_ACCESS_TIMEOUT_SECONDS,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(
                "Live permission check failed for user %s on guild %s: %s",
                session.user_id,
                guild_id,
                e,
            )
            raise HTTPException(
                status_code=503,
                detail="Impossible de vérifier vos permissions sur ce serveur — réessayez.",
            ) from e
        for stale in [
            k for k, (at, _) in self._access_cache.items() if now - at >= GUILD_ACCESS_TTL_SECONDS
        ]:
            del self._access_cache[stale]
        self._access_cache[key] = (now, access)
        return access

    async def user_manages_guild(self, session: Session, guild_id: str | int) -> bool:
        """True if the user owns/administers/manages *this* guild right now (or is developer).

        Without a bot client (standalone dashboard) the login-time OAuth
        snapshot is the only source; with one, the live member is authoritative.
        """
        if self.oauth.is_developer(session):
            return True
        if not self.bot:
            managed = {g["id"] for g in self.oauth.get_user_managed_guilds(session)}
            return str(guild_id) in managed
        return (await self.guild_access(session, guild_id)).manages_guild

    async def require_guild_admin(self, request: Request, guild_id: str | int) -> Session:
        """Per-guild authorization — managing one guild grants nothing on the others."""
        session = self.require_session(request)
        if not await self.user_manages_guild(session, guild_id):
            raise HTTPException(
                status_code=403, detail="Accès réservé aux administrateurs de ce serveur"
            )
        return session

    def require_developer(self, request: Request) -> Session:
        session = self.require_session(request)
        if not self.oauth.is_developer(session):
            raise HTTPException(status_code=403, detail="Accès réservé au développeur")
        return session

    # --- Extension introspection -------------------------------------

    def get_extension_module_paths(self) -> list[str]:
        """Return module paths (e.g. ``extensions.tricount``) for loaded extensions."""
        paths = []
        if self.bot and hasattr(self.bot, "ext"):
            for _, ext_instance in self.bot.ext.items():
                module_path = getattr(ext_instance, "extension_name", None)
                if module_path:
                    paths.append(module_path)
                else:
                    mod = type(ext_instance).__module__
                    if mod:
                        paths.append(mod)
        return paths


# ---------------------------------------------------------------------------
# Module discovery (shared between /api/modules and the auto-reload helper)
# ---------------------------------------------------------------------------


def iter_extension_source_files(ext_dir: str, entry: str):
    """Yield ``*.py`` source files that belong to an extension entry.

    An entry may be either ``<name>.py`` (single-file extension) or a package
    directory with ``__init__.py`` — in which case every ``*.py`` within is
    yielded so ``load_config(...)`` calls in any submodule are picked up.
    """
    if entry.startswith("_") or entry.startswith("__"):
        return
    full = os.path.join(ext_dir, entry)
    if entry.endswith(".py") and os.path.isfile(full):
        yield full
    elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
        for root, _, files in os.walk(full):
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(root, name)


def discover_modules() -> list[str]:
    """Discover all module names used by extensions via ``load_config("...")``."""
    import re

    modules: set[str] = set()
    ext_dir = "extensions"
    if os.path.isdir(ext_dir):
        for fname in os.listdir(ext_dir):
            for fpath in iter_extension_source_files(ext_dir, fname):
                try:
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                    for match in re.finditer(r'load_config\(["\']([\w]+)["\']\)', content):
                        modules.add(match.group(1))
                except Exception:
                    pass
    return sorted(modules)


def build_module_to_extension_map() -> dict[str, list[str]]:
    """Map module config names to the extension module paths that read them.

    E.g. ``{"moduleTricount": ["extensions.tricount"]}``. A module can back
    several extensions: ``moduleUtils`` is read by admin, polls and reminders.
    """
    import re

    mapping: dict[str, list[str]] = {}
    ext_dir = "extensions"
    if not os.path.isdir(ext_dir):
        return mapping
    for fname in sorted(os.listdir(ext_dir)):
        if fname.startswith("_") or fname.startswith("__"):
            continue
        full = os.path.join(ext_dir, fname)
        if fname.endswith(".py") and os.path.isfile(full):
            ext_module_path = f"extensions.{fname[:-3]}"
        elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
            ext_module_path = f"extensions.{fname}"
        else:
            continue
        for fpath in iter_extension_source_files(ext_dir, fname):
            try:
                with open(fpath, encoding="utf-8") as f:
                    content = f.read()
                for match in re.finditer(r'load_config\(["\']([\w]+)["\']\)', content):
                    paths = mapping.setdefault(match.group(1), [])
                    if ext_module_path not in paths:
                        paths.append(ext_module_path)
            except Exception:
                pass
    return mapping
