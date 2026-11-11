"""Paginator and reaction-poll formatter.

- :class:`CustomPaginator` — drop-in replacement for ``interactions.ext.paginators.Paginator``
  with a ``callback`` button, select-dropdown jumps, and keep-alive pings.
- :func:`format_poll` — mutates a poll embed in place to render vote counts per
  option and the voters behind each one, keyed off the reaction payload.
"""

from __future__ import annotations

import time

from interactions import ComponentContext, Message
from interactions.api.events import MessageReactionAdd, MessageReactionRemove
from interactions.ext import paginators

from features.polls import poll_option_emojis, render_reaction_poll
from src.core.config import load_discord2name
from src.discord_ext.messages import copy_embed

# ---------------------------------------------------------------------------
# Custom paginator
# ---------------------------------------------------------------------------


class CustomPaginator(paginators.Paginator):
    """Custom paginator with overridden button handling."""

    def set_pages(self, pages: list, *, page_index: int | None = None) -> None:
        """Swap in freshly rendered pages, keeping this paginator's identity.

        Building a new ``Paginator`` mints a new ``_uuid``, which every button
        ``custom_id`` is derived from — so a refresher that rebuilt its
        paginator each cycle handed the message different components every
        time, and no change check could ever find the render unchanged. Worse,
        each construction registers another component callback under that uuid
        and ``Client.add_component_callback`` raises on duplicates, so the old
        ones can only accumulate.

        Reusing one instance per persistent message keeps the custom_ids
        stable, keeps a single live callback, and leaves the buttons wired to
        the pages currently on screen.
        """
        self.pages = list(pages)
        if page_index is not None:
            self.page_index = page_index
        self.page_index = max(0, min(self.page_index, len(self.pages) - 1))

    async def _on_button(self, ctx: ComponentContext, *args, **kwargs) -> Message | None:
        if self._timeout_task:
            self._timeout_task.ping.set()
        match ctx.custom_id.split("|")[1]:
            case "first":
                self.page_index = 0
            case "last":
                self.page_index = len(self.pages) - 1
            case "next":
                if (self.page_index + 1) < len(self.pages):
                    self.page_index += 1
            case "back":
                if self.page_index >= 1:
                    self.page_index -= 1
            case "select":
                self.page_index = int(ctx.values[0])
            case "callback":
                if self.callback is not None:
                    return await self.callback(ctx)

        await ctx.edit_origin(**self.to_dict())
        return None


# ---------------------------------------------------------------------------
# Poll formatter
# ---------------------------------------------------------------------------

# Per-guild cache of the ``discord2name`` id → display-name overrides. Keeps
# ``format_poll`` from hitting the JSON file on every reaction while still
# picking up config edits after the TTL; entries are bounded by guild count.
name_cache: dict[str, dict[str, str]] = {}
_name_cache_loaded_at: dict[str, float] = {}
_NAME_CACHE_TTL_SECONDS = 600.0


def _guild_name_overrides(server_id: str) -> dict[str, str]:
    """Return the discord2name mapping for a guild, reloading it after the TTL."""
    now = time.monotonic()
    loaded_at = _name_cache_loaded_at.get(server_id)
    if loaded_at is None or now - loaded_at > _NAME_CACHE_TTL_SECONDS:
        name_cache[server_id] = load_discord2name(server_id)
        _name_cache_loaded_at[server_id] = now
    return name_cache[server_id]


async def format_poll(event: MessageReactionAdd | MessageReactionRemove):
    """Render the current vote counts + voter names into a poll embed.

    The embed's description is the "\\n\\n"-separated option list the poll
    command produced. Only the reactions of the poll's own option emojis are
    read. Each call replaces the description of a copy and returns it (the
    message is left untouched).
    """
    message = event.message
    embed = copy_embed(message.embeds[0])
    description = embed.description or ""
    option_emojis = poll_option_emojis(description)
    overrides = _guild_name_overrides(str(message.guild.id))

    voters: dict[str, list[str]] = {}
    participants: set[int] = set()
    for reaction in message.reactions:
        emoji = str(reaction.emoji)
        if emoji not in option_emojis:
            continue
        users = [user for user in await reaction.users().flatten() if not user.bot]
        voters[emoji] = [overrides.get(str(user.id), user.display_name) for user in users]
        participants.update(user.id for user in users)

    embed.description = render_reaction_poll(description, voters, len(participants))
    return embed


__all__ = ["CustomPaginator", "format_poll", "name_cache"]
