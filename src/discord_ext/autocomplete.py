"""Autocomplete handlers."""

from __future__ import annotations

from collections.abc import Callable

from interactions import AutocompleteContext


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

    groups = await col_func(ctx.guild.id).find(query).to_list()
    input_text = ctx.input_text.lower()
    filtered = [
        {"name": g["name"], "value": g["name"]} for g in groups if input_text in g["name"].lower()
    ]
    await ctx.send(choices=filtered[:25])


__all__ = ["guild_group_autocomplete"]
