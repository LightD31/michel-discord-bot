"""Embed primitives: color palette, spacer field, Discord timestamp formatter.

Extensions should import from here instead of hard-coding hex literals — the
palette is the source of truth. A few constants (e.g. ``Colors.SPOTIFY``) match
the corresponding brand; the rest mirror the status-indicator conventions
already used across the codebase (green success / red error / blue info / …).
"""

from datetime import datetime


class Colors:
    """Centralized embed color palette used across extensions."""

    SUCCESS = 0x00FF00
    ERROR = 0xFF0000
    INFO = 0x0099FF
    WARNING = 0xFF9900
    ORANGE = 0xFFA500
    SPOTIFY = 0x1DB954
    TWITCH = 0x6441A5
    TWITCH_ALT = 0x9146FF
    VLR = 0xE04747
    CONFRERIE = 0x9B462E
    COLOC = 0x05B600
    FEUR = 0x9B59B6
    UTIL = 0x3489EB
    XP = 0x00FF00
    SECRET_SANTA = 0xFF0000
    SECRET_SANTA_SUCCESS = 0x00FF00
    SECRET_SANTA_ACCENT = 0xFF00FF
    BACKUP_SUCCESS = 0x2ECC71
    BACKUP_ERROR = 0xE74C3C


# Zero-width space field — useful to force an empty cell in a 3-column embed.
SPACER_FIELD = {"name": "\u200b", "value": "\u200b", "inline": True}


def format_discord_timestamp(dt: datetime, style: str = "f") -> str:
    """Format a ``datetime`` as a Discord dynamic timestamp.

    Common styles: ``f`` (full), ``F`` (full + weekday), ``R`` (relative),
    ``t`` (short time), ``d`` (short date).
    """
    return f"<t:{int(dt.timestamp())}:{style}>"


__all__ = ["Colors", "SPACER_FIELD", "format_discord_timestamp"]
