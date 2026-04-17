"""Shared helpers to avoid code duplication across extensions."""

import logging
import random
from datetime import datetime
from typing import Any, Callable, Optional

from interactions import (
    AutocompleteContext,
    Client,
    Embed,
    Message,
    SlashContext,
)


# ---------------------------------------------------------------------------
# Embed color constants
# ---------------------------------------------------------------------------

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


async def send_success(ctx: SlashContext, message: str) -> None:
    """Send an ephemeral success message prefixed with a check emoji."""
    await ctx.send(f"✅ {message}", ephemeral=True)


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


# ---------------------------------------------------------------------------
# Module enabled check
# ---------------------------------------------------------------------------

def is_guild_enabled(guild_id: int | str, enabled_servers: list[str]) -> bool:
    """Return True if the guild is in the enabled servers list."""
    return str(guild_id) in enabled_servers


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
    logger: Optional[logging.Logger] = None,
) -> Optional[Message]:
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
    except Exception as e:
        if logger:
            logger.error("Could not fetch channel %s: %s", channel_id, e)
        return None
    if channel is None or not hasattr(channel, "send"):
        return None

    if message_id:
        try:
            existing = await channel.fetch_message(int(message_id))
            if existing is not None:
                return existing
        except Exception as e:
            if logger:
                logger.warning(
                    "Persistent message %s missing in channel %s (%s); recreating",
                    message_id, channel_id, e,
                )

    try:
        msg = await channel.send(initial_content)
    except Exception as e:
        if logger:
            logger.error("Could not create persistent message in %s: %s", channel_id, e)
        return None

    if pin:
        try:
            await msg.pin()
        except Exception as e:
            if logger:
                logger.warning("Could not pin persistent message %s: %s", msg.id, e)

    if guild_id is not None:
        try:
            from src.config_manager import save_module_field
            save_module_field(module_name, guild_id, message_id_key, str(msg.id))
        except Exception as e:
            if logger:
                logger.error("Could not save message id to config: %s", e)

    return msg


