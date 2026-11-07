"""Bind the log context for each command, button, select and modal.

Installed as the client's ``global_pre_run_callback`` (``main.py``), which
interactions.py awaits in the same task, just before the callback. Everything
the interaction logs then carries its guild, its command and a fresh
correlation id, and so do the error events it dispatches: those tasks copy
the context. ``extensions/errors.py`` shows that id to the member as the
error reference.
"""

from __future__ import annotations

from typing import Any

from interactions import ComponentContext, ModalContext

from src.core import log_context


def describe(ctx: Any) -> str:
    """``/command sub``, ``component:<custom_id>`` or ``modal:<custom_id>``."""
    if isinstance(ctx, ComponentContext):
        return f"component:{ctx.custom_id}"
    if isinstance(ctx, ModalContext):
        return f"modal:{ctx.custom_id}"
    target = getattr(ctx, "invoke_target", None)
    return f"/{target}" if target else type(ctx).__name__


async def bind_interaction_context(ctx: Any, **_: Any) -> None:
    """``global_pre_run_callback``: tag what the interaction logs from here on."""
    guild_id = getattr(ctx, "guild_id", None)
    log_context.bind(
        correlation_id=log_context.new_id(),
        guild_id=guild_id if guild_id else None,
        command=describe(ctx),
    )


__all__ = ["bind_interaction_context", "describe"]
