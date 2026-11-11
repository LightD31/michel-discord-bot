"""Channels a guild's settings may name: only that guild's own.

A channel id in a guild's config, or one sent by the dashboard, is chosen by
that guild's admins. ``bot.fetch_channel`` answers for any channel the bot can
see, in any guild, so an id copied from another server would make the bot
post there with its own permissions. Resolve such ids through
:func:`fetch_guild_channel` instead.
"""

from __future__ import annotations

import logging
from typing import Any

from interactions import Client, Permissions


def channel_guild_id(channel: Any) -> str | None:
    """The id of the guild *channel* belongs to, ``None`` for a DM or unknown."""
    gid = getattr(channel, "_guild_id", None)
    if gid is None:
        gid = getattr(getattr(channel, "guild", None), "id", None)
    return None if gid is None else str(gid)


def channel_in_guild(channel: Any, guild_id: int | str) -> bool:
    """Whether *channel* is one of guild *guild_id*'s channels (threads included)."""
    return channel is not None and channel_guild_id(channel) == str(guild_id)


async def fetch_guild_channel(
    bot: Client,
    guild_id: int | str,
    channel_id: int | str,
    *,
    logger: logging.Logger | None = None,
) -> Any | None:
    """Channel *channel_id* if it exists and belongs to guild *guild_id*, else ``None``.

    Run it on the bot loop. A malformed id, an unknown channel and another
    guild's channel all give ``None``; the last one is logged as a warning,
    since only a hand-edited config or a crafted request produces it.
    """
    try:
        channel = await bot.fetch_channel(int(channel_id))
    except Exception as e:  # noqa: BLE001 — unknown, deleted or inaccessible: same answer
        if logger:
            logger.warning("Guild %s: channel %s unavailable: %s", guild_id, channel_id, e)
        return None
    if channel is None:
        return None
    if not channel_in_guild(channel, guild_id):
        if logger:
            logger.warning(
                "Guild %s: channel %s belongs to guild %s, refused",
                guild_id,
                channel_id,
                channel_guild_id(channel),
            )
        return None
    return channel


def member_can(member: Any, channel: Any, permission: Permissions) -> bool:
    """Whether *member* holds every bit of *permission* in *channel*.

    Counts the channel's overwrites (a thread's are its parent's). A command's
    ``default_member_permissions`` is only checked in the channel it is run
    from, so a command acting on another channel checks the author there too.
    Anything it can't work out (a DM, a channel from another guild, a thread
    whose parent isn't cached) is a no.
    """
    permissions_for = getattr(channel, "permissions_for", None)
    if member is None or not callable(permissions_for):
        return False
    try:
        return permission in permissions_for(member)
    except (AttributeError, RuntimeError, ValueError):  # guild or parent not cached
        return False


__all__ = ["channel_guild_id", "channel_in_guild", "fetch_guild_channel", "member_can"]
