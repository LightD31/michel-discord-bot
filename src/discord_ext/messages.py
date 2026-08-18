"""Ephemeral responses, guild checks, and persistent-message bootstrapping.

Every helper here is a thin wrapper around the ``interactions`` API — the point
is to remove duplicated try/except and ``if not ctx.guild:`` boilerplate that
previously lived in every extension.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from interactions import Client, Embed, Message, SlashContext, ThreadChannel
from interactions.models.discord.components import process_components

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
# Change-aware message edit
# ---------------------------------------------------------------------------

# Payload keys we know how to diff against a message's current state. Anything
# else (files, attachments, flags, …) is opaque, so we edit unconditionally.
_COMPARABLE_KEYS = ("content", "embed", "embeds", "components")
_OPAQUE_KEYS = ("file", "files", "attachments")

# Embed sub-objects whose ``url`` Discord rewrites when the embed points at an
# uploaded file (``attachment://x.png`` becomes a CDN link on the way back).
_EMBED_URL_HOLDERS = ("image", "thumbnail", "footer", "author")
_ATTACHMENT_CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")


def _canonical_attachment_url(url: Any) -> Any:
    """Collapse a CDN attachment link back to its ``attachment://name`` form.

    An embed built locally says ``attachment://stats.png``; the copy Discord
    echoes back says ``https://cdn.discordapp.com/attachments/…/stats.png?ex=…``.
    Without this the two never compare equal and the diff is useless for every
    embed that renders an uploaded image.
    """
    if not isinstance(url, str) or not url:
        return url
    parsed = urlsplit(url)
    if parsed.scheme in ("http", "https"):
        if parsed.netloc not in _ATTACHMENT_CDN_HOSTS:
            return url
        if not parsed.path.startswith("/attachments/"):
            return url
        return f"attachment://{parsed.path.rsplit('/', 1)[-1]}"
    return url


def _normalize_embed_urls(embed: dict[str, Any]) -> dict[str, Any]:
    for key in _EMBED_URL_HOLDERS:
        holder = embed.get(key)
        if not isinstance(holder, dict):
            continue
        for url_key in ("url", "icon_url"):
            if url_key in holder:
                holder[url_key] = _canonical_attachment_url(holder[url_key])
    return embed


def _normalize_embeds(value: Any, *, ignore_timestamp: bool = False) -> list[dict[str, Any]] | None:
    """Render embeds (objects or raw dicts) into comparable plain dicts.

    ``Embed.to_dict()`` already drops the fields Discord injects server-side
    (``type``, ``proxy_url``, ``width``/``height``, ``video``, ``provider``), so
    round-tripping both sides through it makes a locally built embed comparable
    with the one Discord echoed back.
    """
    if value is None:
        return None
    if isinstance(value, dict) or not isinstance(value, Sequence) or isinstance(value, str):
        value = [value]

    out: list[dict[str, Any]] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, dict):
            try:
                item = Embed.from_dict(item)
            except Exception:  # noqa: BLE001 — unparseable: compare the raw dict
                out.append(item)
                continue
        rendered = item.to_dict() if hasattr(item, "to_dict") else item
        if isinstance(rendered, dict):
            rendered = _normalize_embed_urls(rendered)
            if ignore_timestamp:
                rendered.pop("timestamp", None)
        out.append(rendered)
    return out


def _payload_differs(
    message: Message, payload: dict[str, Any], *, ignore_timestamp: bool = False
) -> bool:
    """True when ``payload`` would change what the message currently displays.

    Biased towards ``True``: anything we cannot compare confidently counts as a
    change, so the worst case is the redundant edit we already did before.
    """
    if any(payload.get(key) is not None for key in _OPAQUE_KEYS):
        return True
    if not any(key in payload and payload[key] is not None for key in _COMPARABLE_KEYS):
        return True

    content = payload.get("content")
    if content is not None and (content or "") != (getattr(message, "content", "") or ""):
        return True

    new_embeds = payload.get("embeds")
    if new_embeds is None:
        new_embeds = payload.get("embed")
    if new_embeds is not None and _normalize_embeds(
        new_embeds, ignore_timestamp=ignore_timestamp
    ) != _normalize_embeds(
        getattr(message, "embeds", None) or [], ignore_timestamp=ignore_timestamp
    ):
        return True

    components = payload.get("components")
    return components is not None and process_components(components) != process_components(
        getattr(message, "components", None) or []
    )


async def edit_message_if_changed(
    message: Message,
    *,
    logger: logging.Logger | None = None,
    force: bool = False,
    ignore_timestamp: bool = False,
    **payload: Any,
) -> bool:
    """Edit ``message`` only when the payload actually changes what it shows.

    The order here is the contract every caller relies on:

    1. Diff the requested payload against the message's current state and bail
       out when nothing changed — no API call, and crucially no thread side
       effect (a periodic refresh must not keep resurrecting an archived
       thread just to write the same bytes back).
    2. Only once something *did* change, check whether the message lives in an
       archived thread, and unarchive it if so.
    3. Perform the edit.

    ``force=True`` skips step 1 for callers that already know the payload is
    new (or that pass content we cannot diff, such as freshly rendered images).

    ``ignore_timestamp=True`` drops the embed ``timestamp`` from the comparison.
    Use it on periodic refreshers that stamp ``datetime.now()`` on every render:
    there the timestamp is a "when we last polled" marker, not information, and
    leaving it in makes every cycle look like a change.

    Returns ``True`` when an edit was sent, ``False`` when it was a no-op.
    """
    if not force and not _payload_differs(message, payload, ignore_timestamp=ignore_timestamp):
        if logger:
            logger.debug(
                "Skipping edit of message %s: nothing changed", getattr(message, "id", "?")
            )
        return False

    await unarchive_if_thread(getattr(message, "channel", None), logger=logger)
    await message.edit(**payload)
    return True


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
    "edit_message_if_changed",
    "fetch_or_create_persistent_message",
    "fetch_user_safe",
    "require_guild",
    "send_error",
    "send_success",
    "unarchive_if_thread",
]
