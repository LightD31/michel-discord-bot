"""Autocomplete handlers and guild-state helpers."""

from __future__ import annotations

from typing import Callable

from interactions import AutocompleteContext


def is_guild_enabled(guild_id: int | str, enabled_servers: list[str]) -> bool:
    """Return True if the guild is in the enabled-servers list."""
    return str(guild_id) in enabled_servers


async def guild_group_autocomplete(
    ctx: AutocompleteContext,
    col_func: Callable,
    *,
    member_filter: bool = True,
) -> None:
    """Shared autocomplete handler for guild-scoped group selection.

    *col_func* should be a callable that accepts a guild_id and returns a
    Motor collection (e.g. ``TricountClass._groups_col``). Groups are filtered
    by ``{"is_active": True}`` and, when *member_filter* is true (default),
    by membership of the invoking user.
    """
    if not ctx.guild:
        await ctx.send(choices=[])
        return

    query: dict = {"is_active": True}
    if member_filter:
        query["members"] = ctx.author.id

    groups = await col_func(ctx.guild.id).find(query).to_list(length=None)
    input_text = ctx.input_text.lower()
    filtered = [
        {"name": g["name"], "value": g["name"]}
        for g in groups
        if input_text in g["name"].lower()
    ]
    await ctx.send(choices=filtered[:25])


__all__ = ["guild_group_autocomplete", "is_guild_enabled"]
