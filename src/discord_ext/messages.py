"""Ephemeral responses, guild checks, and persistent-message bootstrapping.

Every helper here is a thin wrapper around the ``interactions`` API — the point
is to remove duplicated try/except and ``if not ctx.guild:`` boilerplate that
previously lived in every extension.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from interactions import Client, Embed, Message, SlashContext, ThreadChannel
from interactions.models.discord.components import process_components

from src.core.errors import DatabaseError
from src.core.message_refs import MessageRefRepository

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


def copy_embed(embed: Embed) -> Embed:
    """Return an independent copy of ``embed`` that is safe to mutate.

    Use it before editing an embed read from ``message.embeds``. Mutating that
    object in place also mutates what the message currently shows, so
    :func:`edit_message_if_changed` compares the new embed with itself, finds
    nothing changed, and silently skips the edit.
    """
    return Embed.from_dict(embed.to_dict())


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

    An embed taken from ``message.embeds`` must go through :func:`copy_embed`
    before it is modified: an in-place edit changes the message's own copy too,
    and the diff then sees no change.

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
# Persistent module message (auto-create + pin + remember where it lives)
# ---------------------------------------------------------------------------

# Messages already resolved in this process, by (guild, slot): the channel each
# was resolved for and the message. Shared by every caller and kept across
# extension reloads, so a slot is looked up in MongoDB once per process.
_resolved: dict[tuple[str, str], tuple[str, Message]] = {}
# One resolution at a time per slot: two refreshes racing on a slot not yet
# resolved would otherwise both post a message.
_slot_locks: dict[tuple[str, str], asyncio.Lock] = {}


async def fetch_or_create_persistent_message(
    bot: Client,
    *,
    guild_id: int | str,
    slot: str,
    channel_id: int | str | None,
    initial_content: str = "Initialisation…",
    pin: bool = False,
    label: str = "",
    logger: logging.Logger | None = None,
) -> Message | None:
    """Return the guild's message for *slot* in *channel_id*, creating it if missing.

    Flow:
      1. Already resolved in this process for this channel: returned as is.
      2. Otherwise the slot's stored reference (``bot_messages`` in the guild's
         database, :mod:`src.core.message_refs`) is fetched, when it points
         into this channel.
      3. On a miss (nothing stored, another channel, deleted), a placeholder
         is sent, optionally pinned, and where it lives is stored.

    An unreadable store gives ``None`` rather than a new message: posting
    while the stored one can't be checked would leave a duplicate behind. The
    caller tries again at its next refresh. *label* names the message in the
    dashboard.
    """
    if not channel_id:
        return None
    gid, wanted = str(guild_id), str(channel_id)
    cached = _resolved.get((gid, slot))
    if cached is not None and cached[0] == wanted:
        return cached[1]
    async with _slot_locks.setdefault((gid, slot), asyncio.Lock()):
        cached = _resolved.get((gid, slot))
        if cached is not None and cached[0] == wanted:
            return cached[1]
        message = await _resolve(bot, gid, slot, wanted, initial_content, pin, label, logger)
        if message is not None:
            _resolved[(gid, slot)] = (wanted, message)
        return message


async def _resolve(
    bot: Client,
    gid: str,
    slot: str,
    wanted: str,
    initial_content: str,
    pin: bool,
    label: str,
    logger: logging.Logger | None,
) -> Message | None:
    repo = MessageRefRepository(gid)
    try:
        ref = await repo.get(slot)
    except (DatabaseError, RuntimeError) as e:
        # RuntimeError: no ``mongodb.url`` at all, raised before the driver.
        if logger:
            logger.error("Guild %s: could not read where message %s lives: %s", gid, slot, e)
        return None

    try:
        channel = await bot.fetch_channel(int(wanted))
    except Exception as e:  # noqa: BLE001 — log and return None
        if logger:
            logger.error("Could not fetch channel %s: %s", wanted, e)
        return None
    if channel is None or not hasattr(channel, "send"):
        return None

    await unarchive_if_thread(channel, logger=logger)

    if ref is not None and ref.channel_id == wanted and ref.message_id:
        try:
            # hasattr check above narrows this at runtime; mypy can't see it.
            existing = await channel.fetch_message(int(ref.message_id))  # type: ignore[union-attr]
            if existing is not None:
                return existing
        except Exception as e:  # noqa: BLE001 — log and recreate
            if logger:
                logger.warning(
                    "Persistent message %s missing in channel %s (%s); recreating",
                    ref.message_id,
                    wanted,
                    e,
                )

    try:
        msg = await channel.send(initial_content)
    except Exception as e:  # noqa: BLE001 — log and return None
        if logger:
            logger.error("Could not create persistent message in %s: %s", wanted, e)
        return None

    if pin:
        try:
            await msg.pin()
        except Exception as e:  # noqa: BLE001 — non-fatal
            if logger:
                logger.warning("Could not pin persistent message %s: %s", msg.id, e)

    try:
        await repo.save(slot, wanted, str(msg.id), label=label)
    except (DatabaseError, RuntimeError) as e:
        if logger:
            logger.error("Guild %s: could not store where message %s lives: %s", gid, slot, e)
    return msg


def forget_persistent_message(guild_id: int | str, slot: str) -> None:
    """Resolve the slot again at its next use (its message was deleted, say)."""
    _resolved.pop((str(guild_id), slot), None)


def cached_persistent_message(guild_id: int | str, slot: str) -> Message | None:
    """The slot's message if this process already resolved it, without any call."""
    entry = _resolved.get((str(guild_id), slot))
    return entry[1] if entry else None


async def republish_persistent_message(
    bot: Client, guild_id: int | str, slot: str, *, logger: logging.Logger | None = None
) -> bool:
    """Delete the slot's message and its reference, so the next refresh posts it anew.

    Used to bring a message back to the bottom of its channel. Returns
    ``False`` when the slot was unknown. Run it on the bot loop.
    """
    ref = await MessageRefRepository(guild_id).delete(slot)
    forget_persistent_message(guild_id, slot)
    if ref is None:
        return False
    try:
        channel = await bot.fetch_channel(int(ref.channel_id))
        message = await channel.fetch_message(int(ref.message_id))  # type: ignore[union-attr]
        if message is not None:
            await message.delete()
    except Exception as e:  # noqa: BLE001 — already gone, or no permission: the slot is reset anyway
        if logger:
            logger.info("Guild %s: old message of %s not deleted: %s", guild_id, slot, e)
    return True


class GuildMessages:
    """One persistent message per guild for a module, kept between refreshes.

    A periodic refresher calls ``await messages.get(guild_id, settings)`` with
    the guild's live module settings; the message lives in the channel named by
    *channel_key*. It is resolved through :func:`fetch_or_create_persistent_message`
    under *slot*: fetched (or created, and where it lives stored) on first use,
    then reused, and resolved again when the configured channel changes, so a
    dashboard edit applies at the next refresh without reloading the
    extension. A guild without a channel gets ``None``. ``keep_only()`` drops
    the guilds where the module was switched off.
    """

    def __init__(
        self,
        bot: Client,
        *,
        slot: str,
        channel_key: str,
        label: str = "",
        initial_content: str = "Initialisation…",
        pin_key: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.bot = bot
        self.slot = slot
        self.channel_key = channel_key
        self.label = label
        self.initial_content = initial_content
        self.pin_key = pin_key
        self.logger = logger
        self._guilds: set[str] = set()

    async def get(self, guild_id: int | str, settings: dict[str, Any]) -> Message | None:
        """The guild's message, fetched or created if not resolved for its channel."""
        gid = str(guild_id)
        channel_id = str(settings.get(self.channel_key) or "").strip()
        if not channel_id:
            self.forget(gid)
            return None
        self._guilds.add(gid)
        return await fetch_or_create_persistent_message(
            self.bot,
            guild_id=gid,
            slot=self.slot,
            channel_id=channel_id,
            initial_content=self.initial_content,
            pin=bool(settings.get(self.pin_key)) if self.pin_key else False,
            label=self.label,
            logger=self.logger,
        )

    def cached(self, guild_id: int | str) -> Message | None:
        """The guild's message if one is already held, without any Discord call."""
        return cached_persistent_message(guild_id, self.slot)

    def forget(self, guild_id: int | str) -> None:
        """Resolve the guild's message again next time (it was deleted, say)."""
        self._guilds.discard(str(guild_id))
        forget_persistent_message(guild_id, self.slot)

    def keep_only(self, guild_ids: Sequence[int | str]) -> None:
        """Drop the messages of every guild not in *guild_ids*."""
        keep = {str(g) for g in guild_ids}
        for gid in [g for g in self._guilds if g not in keep]:
            self.forget(gid)


__all__ = [
    "GuildMessages",
    "copy_embed",
    "edit_message_if_changed",
    "cached_persistent_message",
    "fetch_or_create_persistent_message",
    "fetch_user_safe",
    "forget_persistent_message",
    "republish_persistent_message",
    "require_guild",
    "send_error",
    "send_success",
    "unarchive_if_thread",
]
