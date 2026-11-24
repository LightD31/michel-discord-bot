"""A stand-in for :class:`src.webui.context.WebUIContext` in route tests (P39).

Routes take their tiers, config and bot from the context; this one gives them
what a test sets up, and records what they wrote. A test whose context does
something special subclasses it rather than copying it.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

ADMIN = SimpleNamespace(username="admin", user_id="1")
DEVELOPER = SimpleNamespace(username="dev", user_id="2")


def member_session(user_id: str, guilds: list[dict[str, str]]) -> SimpleNamespace:
    """A member's session: ``user_id``, and the guilds Discord listed at login."""
    return SimpleNamespace(user_id=user_id, username="moi", guilds=guilds)


class FakeWebUI:
    """The dashboard's context as a route sees it.

    *config* is the whole ``config.json`` (``{"config": …, "servers": …}``).
    *manages* lists the guilds the session administers (``None``: every one;
    set it to ``set()`` to refuse them all),
    *member_of* those it is a member of, *developer* whether it passes
    ``require_developer``. ``mutate_config`` is atomic like the real one: a
    mutator that raises writes nothing. ``call_bot`` runs the function on the
    test's loop.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        session: Any = ADMIN,
        bot: Any = None,
        manages: Iterable[str] | None = None,
        member_of: Iterable[str] = (),
        developer: bool = False,
    ) -> None:
        self.data: dict[str, Any] = config if config is not None else {"config": {}, "servers": {}}
        self.session = session
        self.bot = bot
        self.manages = None if manages is None else {str(g) for g in manages}
        self.member_of = {str(g) for g in member_of}
        self.developer = developer
        self.written: list[dict[str, Any]] = []
        self.checked: list[str] = []  # the guilds require_guild_admin was asked about
        self.oauth = SimpleNamespace(is_developer=lambda _session: self.developer)

    @classmethod
    def with_server(cls, guild_id: str, server: dict[str, Any] | None = None, **kwargs: Any):
        """A context whose config holds one server's settings."""
        return cls({"config": {}, "servers": {str(guild_id): server or {}}}, **kwargs)

    # ── Tiers ────────────────────────────────────────────────────────

    def require_session(self, request: Any) -> Any:  # noqa: ARG002 — signature parity
        return self.session

    async def require_guild_admin(self, request: Any, guild_id: Any) -> Any:  # noqa: ARG002
        self.checked.append(str(guild_id))
        if self.manages is not None and str(guild_id) not in self.manages:
            raise HTTPException(status_code=403, detail="no")
        return self.session

    async def user_is_member(self, session: Any, guild_id: Any) -> bool:  # noqa: ARG002
        return str(guild_id) in self.member_of

    async def require_guild_member(self, request: Any, guild_id: Any) -> Any:  # noqa: ARG002
        if str(guild_id) not in self.member_of:
            raise HTTPException(status_code=403, detail="Tu n'es pas membre de ce serveur.")
        return self.session

    def require_developer(self, request: Any) -> Any:  # noqa: ARG002
        if not self.developer:
            raise HTTPException(status_code=403, detail="no")
        return self.session

    # ── Config ───────────────────────────────────────────────────────

    def get_full_config(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

    def mutate_config(self, mutator: Any) -> dict[str, Any]:
        data = copy.deepcopy(self.data)
        mutator(data)  # raising here means nothing is written
        self.data = data
        self.written.append(copy.deepcopy(data))
        return data

    @property
    def writes(self) -> int:
        return len(self.written)

    # ── The bot ──────────────────────────────────────────────────────

    def bot_loop_alive(self) -> bool:
        return False

    async def call_bot(self, func: Any, *args: Any, what: str = "", **kwargs: Any) -> Any:  # noqa: ARG002
        return await func(*args, **kwargs)

    def module_extension_map(self, *, on_save: bool = False) -> dict[str, Any]:  # noqa: ARG002
        return {}


__all__ = ["ADMIN", "DEVELOPER", "FakeWebUI", "member_session"]
