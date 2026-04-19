"""Ephemeral responses, guild checks, and persistent-message bootstrapping.

Every helper here is a thin wrapper around the ``interactions`` API — the point
is to remove duplicated try/except and ``if not ctx.guild:`` boilerplate that
previously lived in every extension.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from interactions import Client, Message, SlashContext, ThreadChannel

# ---------------------------------------------------------------------------
# Ephemeral responses
# ---------------------------------------------------------------------------


async def send_error(ctx: SlashContext, message: str) -> None:
    """Send an ephemeral error message prefixed with a cross emoji."""
    await ctx.send(f"❌ {message}", ephemeral=True)


async def send_success(ctx: SlashContext, message: str) -> None:
    """Send an ephemeral success message prefixed with a check emoji."""
    await ctx.send(f"✅ {message}", ephemeral=True)


# ---------------------------------------------------------------------------
# Guild check
# ---------------------------------------------------------------------------


async def require_guild(ctx: SlashContext) -> bool:
    """Return True if the command is used inside a guild, else send an error.

    Usage::

        if not await require_guild(ctx):
            return
    """
    if not ctx.guild:
        await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
        return False
    return True


# ---------------------------------------------------------------------------
# Safe user fetch (cache → API fallback)
# ---------------------------------------------------------------------------


async def fetch_user_safe(bot: Client, user_id) -> tuple[str, Any]:
    """Try the cache then the API.

    Returns ``(display_name, user_or_None)`` — never raises.
    """
    user = bot.get_user(user_id)
    if not user:
        with contextlib.suppress(Exception):  # noqa: BLE001 — graceful fallback
            user = await bot.fetch_user(user_id)
    name = user.display_name if user else f"ID:{user_id}"
    return name, user


# ---------------------------------------------------------------------------
# Thread unarchive
# ---------------------------------------------------------------------------


async def unarchive_if_thread(
    channel: Any,
    logger: logging.Logger | None = None,
) -> None:
    """Unarchive the channel if it is an archived thread. No-op otherwise.

    Discord rejects sends/edits against archived threads, so call this before
    any send_message/edit_message when the target could be a thread.
    """
    if not isinstance(channel, ThreadChannel):
        return
    if not getattr(channel, "archived", False):
        return
    try:
        await channel.edit(archived=False, reason="Auto-unarchive before send/edit")
    except Exception as e:  # noqa: BLE001 — log and continue; not fatal
        if logger:
            logger.warning("Could not unarchive thread %s: %s", getattr(channel, "id", "?"), e)


# ---------------------------------------------------------------------------
# Persistent module message (auto-create + pin + persist id)
# ---------------------------------------------------------------------------


async def fetch_or_create_persistent_message(
    bot: Client,
    *,
    channel_id: int | str | None,
    message_id: int | str | None,
    module_name: str,
    message_id_key: str,
    guild_id: int | str | None = None,
    initial_content: str = "Initialisation…",
    pin: bool = False,
    logger: logging.Logger | None = None,
) -> Message | None:
    """Return the module's persistent Discord message, creating it if missing.

    Flow:
      1. If ``message_id`` is set, try to fetch it from ``channel_id``.
      2. On miss (deleted, None, error), send a placeholder in the channel,
         optionally pin it, and persist the new id in ``config.json`` under
         ``servers.<guild_id>.<module_name>.<message_id_key>``.

    Callers should store the returned Message and edit it going forward.
    """
    if not channel_id:
        return None

    try:
        channel = await bot.fetch_channel(int(channel_id))
    except Exception as e:  # noqa: BLE001 — log and return None
        if logger:
            logger.error("Could not fetch channel %s: %s", channel_id, e)
        return None
    if channel is None or not hasattr(channel, "send"):
        return None

    await unarchive_if_thread(channel, logger=logger)

    if message_id:
        try:
            # hasattr check above narrows this at runtime; mypy can't see it.
            existing = await channel.fetch_message(int(message_id))  # type: ignore[union-attr]
            if existing is not None:
                return existing
        except Exception as e:  # noqa: BLE001 — log and recreate
            if logger:
                logger.warning(
                    "Persistent message %s missing in channel %s (%s); recreating",
                    message_id,
                    channel_id,
                    e,
                )

    try:
        msg = await channel.send(initial_content)
    except Exception as e:  # noqa: BLE001 — log and return None
        if logger:
            logger.error("Could not create persistent message in %s: %s", channel_id, e)
        return None

    if pin:
        try:
            await msg.pin()
        except Exception as e:  # noqa: BLE001 — non-fatal
            if logger:
                logger.warning("Could not pin persistent message %s: %s", msg.id, e)

    if guild_id is not None:
        try:
            from src.core.config import save_module_field

            save_module_field(module_name, guild_id, message_id_key, str(msg.id))
        except Exception as e:  # noqa: BLE001 — log and continue
            if logger:
                logger.error("Could not save message id to config: %s", e)

    return msg


__all__ = [
    "fetch_or_create_persistent_message",
    "fetch_user_safe",
    "require_guild",
    "send_error",
    "send_success",
    "unarchive_if_thread",
]
