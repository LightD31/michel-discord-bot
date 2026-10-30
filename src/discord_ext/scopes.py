"""Slash-command scopes for module commands.

A module's commands are registered only on the guilds where the module is
enabled, so they never show up on a server that doesn't use them. That takes
more than ``scopes=enabled_servers``: interactions.py's command decorators read
``scopes or [GLOBAL_SCOPE]``, so a module enabled on *no* server would publish
its commands globally — on every server, the opposite of what was asked.
:func:`guild_scopes` returns a list that survives that fallback, and an empty
one registers the command nowhere.
"""

from __future__ import annotations

from collections.abc import Iterable

from interactions import Snowflake_Type


class _GuildScopes(list[Snowflake_Type]):
    """Always truthy, so ``scopes or [GLOBAL_SCOPE]`` keeps an empty list as is.

    The command stores a plain copy (its ``scopes`` converter rebuilds the
    list), so the override never leaks past the decorator call.
    """

    def __bool__(self) -> bool:
        return True


def guild_scopes(guild_ids: Iterable[int | str | None]) -> list[Snowflake_Type]:
    """Scopes for ``slash_command(scopes=...)``: these guilds and nothing else.

    *guild_ids* is typically the ``enabled_servers`` list from
    ``load_config(module)``; blank entries (an unset ``devGuildId``) are
    skipped, and so is ``0``, which is the global scope itself. With no guild
    left, the command is registered nowhere instead of globally. Pass the
    result straight to the decorator: it is always truthy, so don't test it for
    emptiness.
    """
    scopes = _GuildScopes()
    for guild_id in guild_ids:
        text = "" if guild_id is None else str(guild_id).strip()
        if text and int(text):
            scopes.append(int(text))
    return scopes
