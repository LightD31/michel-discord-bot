"""A guild's channels for the dashboard's channel pickers.

Runs on the bot loop (``ctx.call_bot``): text and news channels, the threads
the gateway knows plus the active ones (private threads the bot can see
included), and the categories they sit in, each sorted by position.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from interactions import ChannelType

from src.core import logging as logutil

logger = logutil.init_logger("webui.channels")

TEXT_TYPES = frozenset({ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS})
THREAD_TYPES = frozenset(
    {
        ChannelType.GUILD_NEWS_THREAD,
        ChannelType.GUILD_PUBLIC_THREAD,
        ChannelType.GUILD_PRIVATE_THREAD,
    }
)


def channel_entry(channel: Any, *, is_thread: bool, position: int = 0) -> dict:
    """One channel or thread as the picker shows it."""
    cid = str(channel.id)
    parent = getattr(channel, "parent_id", None) or getattr(channel, "category_id", None)
    return {
        "id": cid,
        "name": getattr(channel, "name", cid),
        "parent_id": str(parent) if parent else None,
        "position": position,
        "is_thread": is_thread,
        "archived": bool(getattr(channel, "archived", False)),
    }


def category_entry(channel: Any) -> dict:
    cid = str(channel.id)
    return {
        "id": cid,
        "name": getattr(channel, "name", cid),
        "position": getattr(channel, "position", 0) or 0,
    }


async def list_channels(bot: Any, guild_id: str) -> dict:
    """``{"channels": [...], "categories": [...]}`` for *guild_id*; 404 if the bot isn't there."""
    try:
        guild = await bot.fetch_guild(int(guild_id))
    except Exception as e:
        logger.warning("Guild %s not available for its channels: %s", guild_id, e)
        raise HTTPException(status_code=404, detail="Serveur introuvable") from e
    if guild is None:
        raise HTTPException(status_code=404, detail="Serveur introuvable")
    try:
        channels = await guild.fetch_channels()
    except Exception as e:  # noqa: BLE001 — the gateway cache is a fine fallback
        logger.debug("Fetching the channels of %s failed, using the cache: %s", guild_id, e)
        channels = getattr(guild, "channels", None) or []

    result: list[dict] = []
    categories: list[dict] = []
    for channel in channels:
        ctype = getattr(channel, "type", None)
        if ctype == ChannelType.GUILD_CATEGORY:
            categories.append(category_entry(channel))
        elif ctype in TEXT_TYPES or ctype in THREAD_TYPES:
            result.append(
                channel_entry(
                    channel,
                    is_thread=ctype in THREAD_TYPES,
                    position=getattr(channel, "position", 0) or 0,
                )
            )

    try:
        thread_list = await guild.fetch_active_threads()
        active_threads = getattr(thread_list, "threads", None) or []
    except Exception as e:  # noqa: BLE001 — the listed channels are still worth returning
        logger.debug("Could not fetch active threads for %s: %s", guild_id, e)
        active_threads = []
    known = {c["id"] for c in result}
    for thread in active_threads:
        if str(thread.id) not in known:
            result.append(channel_entry(thread, is_thread=True))
            known.add(str(thread.id))

    result.sort(key=lambda c: (c["position"], c["name"]))
    categories.sort(key=lambda c: c["position"])
    return {"channels": result, "categories": categories}


__all__ = ["THREAD_TYPES", "TEXT_TYPES", "category_entry", "channel_entry", "list_channels"]
