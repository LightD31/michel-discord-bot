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
import os
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from src.webui.auth import DiscordOAuth, Session

COOKIE_NAME = "michel_session"


@dataclass
class WebUIContext:
    """Everything a route handler needs besides the request itself."""

    bot: Any
    bot_loop: asyncio.AbstractEventLoop | None
    oauth: DiscordOAuth

    # --- Config I/O ---------------------------------------------------

    def get_full_config(self) -> dict:
        """Load the full config from disk; tolerate a missing file."""
        from src.core.config import load_full_config

        return load_full_config() or {"config": {}, "servers": {}}

    def save_config(self, data: dict) -> None:
        """Persist *data* atomically and notify ConfigStore subscribers."""
        from src.core.config import config_store

        config_store.save_full(data)

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
        """Admin if user has MANAGE_GUILD/ADMINISTRATOR on any bot guild."""
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


def build_module_to_extension_map() -> dict[str, str]:
    """Map module config names to extension module paths.

    E.g. ``{"moduleTricount": "extensions.tricount"}``.
    """
    import re

    mapping: dict[str, str] = {}
    ext_dir = "extensions"
    if not os.path.isdir(ext_dir):
        return mapping
    for fname in os.listdir(ext_dir):
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
                    mapping[match.group(1)] = ext_module_path
            except Exception:
                pass
    return mapping
