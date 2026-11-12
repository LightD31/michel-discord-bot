"""Autocomplete handlers."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from interactions import AutocompleteContext

from src.core.text import clip

# Discord shows at most 25 choices, each named in 1 to 100 characters.
MAX_CHOICES = 25
CHOICE_NAME_LIMIT = 100


def matching_choices(items: Iterable[tuple[str, str]], typed: str) -> list[dict[str, str]]:
    """Choices for the ``(label, value)`` pairs whose label or value contains *typed*.

    The value is what the option receives (a message id, say), so a member who
    pastes an id still sees it offered. Labels are clipped to Discord's limit.
    """
    needle = typed.strip().lower()
    choices = [
        {"name": clip(label, CHOICE_NAME_LIMIT), "value": value}
        for label, value in items
        if needle in label.lower() or needle in value
    ]
    return choices[:MAX_CHOICES]


async def guild_group_autocomplete(
    ctx: AutocompleteContext,
    col_func: Callable,
    *,
    member_filter: bool = True,
) -> None:
    """Shared autocomplete handler for guild-scoped group selection.

    *col_func* should be a callable that accepts a guild_id and returns a
    MongoDB collection (e.g. ``TricountClass._groups_col``). Groups are filtered
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


__all__ = ["CHOICE_NAME_LIMIT", "MAX_CHOICES", "guild_group_autocomplete", "matching_choices"]
