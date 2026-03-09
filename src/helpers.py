"""Shared helpers to avoid code duplication across extensions."""

import random
from datetime import datetime
from typing import Any, Callable, Optional

from interactions import AutocompleteContext, Client, SlashContext


# ---------------------------------------------------------------------------
# Embed color constants
# ---------------------------------------------------------------------------

class Colors:
    """Centralized embed color palette used across extensions."""
    SUCCESS = 0x00FF00
    ERROR = 0xFF0000
    INFO = 0x0099FF
    WARNING = 0xFF9900
    SPOTIFY = 0x1DB954
    TWITCH = 0x6441A5
    VLR = 0xE04747
    CONFRERIE = 0x9B462E
    COLOC = 0x05B600
    BACKUP_SUCCESS = 0x2ECC71
    BACKUP_ERROR = 0xE74C3C


# ---------------------------------------------------------------------------
# Embed spacer field (zero-width space)
# ---------------------------------------------------------------------------

SPACER_FIELD = {"name": "\u200b", "value": "\u200b", "inline": True}


# ---------------------------------------------------------------------------
# Ephemeral error / guild check
# ---------------------------------------------------------------------------

async def send_error(ctx: SlashContext, message: str) -> None:
    """Send an ephemeral error message prefixed with a cross emoji."""
    await ctx.send(f"❌ {message}", ephemeral=True)


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
    """Try the cache then the API.  Returns *(display_name, user_or_None)*."""
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            pass
    name = user.display_name if user else f"ID:{user_id}"
    return name, user


# ---------------------------------------------------------------------------
# Weighted random message picker
# ---------------------------------------------------------------------------

def pick_weighted_message(
    config: dict,
    list_key: str,
    weights_key: str,
    default: str,
    **format_kwargs,
) -> str:
    """Pick a random message from *config[list_key]* using weights, then format it.

    Example::

        msg = pick_weighted_message(
            srv_cfg,
            "birthdayMessageList", "birthdayMessageWeights",
            "Joyeux anniversaire {mention} !",
            mention=member.mention, age=age,
        )
    """
    messages = config.get(list_key, [default])
    weights = config.get(weights_key, [1] * len(messages))
    chosen = random.choices(messages, weights=weights)[0]
    return chosen.format(**format_kwargs)


# ---------------------------------------------------------------------------
# Discord timestamp formatting
# ---------------------------------------------------------------------------

def format_discord_timestamp(dt: datetime, style: str = "f") -> str:
    """Format a *datetime* as a Discord dynamic timestamp.

    Common styles: ``f`` (full), ``F`` (full + weekday), ``R`` (relative),
    ``t`` (short time), ``d`` (short date).
    """
    return f"<t:{int(dt.timestamp())}:{style}>"


# ---------------------------------------------------------------------------
# Tricount-style group autocomplete helper
# ---------------------------------------------------------------------------

async def guild_group_autocomplete(
    ctx: AutocompleteContext,
    col_func: Callable,
    *,
    member_filter: bool = True,
) -> None:
    """Shared autocomplete handler for guild group selection.

    *col_func* should be a callable that accepts a guild_id and returns
    a Motor collection (e.g. ``TricountClass._groups_col``).
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
