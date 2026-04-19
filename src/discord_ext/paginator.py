"""Paginator and reaction-poll formatter.

- :class:`CustomPaginator` — drop-in replacement for ``interactions.ext.paginators.Paginator``
  with a ``callback`` button, select-dropdown jumps, and keep-alive pings.
- :func:`format_poll` — mutates a poll embed in place to render vote counts per
  option and the voters behind each one, keyed off the reaction payload.
"""

from __future__ import annotations

from collections import defaultdict

from interactions import ComponentContext, Message
from interactions.api.events import MessageReactionAdd, MessageReactionRemove
from interactions.ext import paginators

from src.core.config import load_discord2name

# ---------------------------------------------------------------------------
# Custom paginator
# ---------------------------------------------------------------------------


class CustomPaginator(paginators.Paginator):
    """Custom paginator with overridden button handling."""

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

# Per-guild cache of Discord-id → display-name overrides. Populated lazily from
# the ``discord2name`` config mapping; keeps ``format_poll`` from hitting the
# JSON file on every reaction.
name_cache: dict[str, dict[str, str]] = {}


async def format_poll(event: MessageReactionAdd | MessageReactionRemove):
    """Render the current vote counts + voter names into a poll embed.

    The embed's description is expected to be the "\\n\\n"-separated option
    list initially produced by the poll command. Each call replaces the
    description in place and returns the (mutated) embed.
    """
    message = event.message
    embed = message.embeds[0]
    options = (embed.description or "").split("\n\n")
    reactions = message.reactions

    reaction_users = defaultdict(list)
    reaction_counts = defaultdict(int)
    max_reaction_count = 0
    for reaction in reactions:
        users = [user for user in await reaction.users().flatten() if not user.bot]
        reaction_users[str(reaction.emoji)] = users
        reaction_counts[str(reaction.emoji)] = reaction.count - 1
        if reaction.count > max_reaction_count:
            max_reaction_count = reaction.count - 1

    max_reaction_indices = [
        i for i, count in reaction_counts.items() if count == max_reaction_count
    ]
    participant_count = len(
        {user.id for users in reaction_users.values() for user in users} - {message.author.id}
    )

    description_list = []
    for _i, option in enumerate(options):
        option = option.split(":", 1)[0].replace("**", "")
        emoji_str = option.split(" ", 1)[0]

        reaction_count = reaction_counts[emoji_str]
        user_list = reaction_users[emoji_str]

        user_names = []
        for user in user_list:
            user_name = user.display_name
            user_id = str(user.id)
            server_id = str(event.message.guild.id)
            if server_id not in name_cache:
                name_cache[server_id] = {}
            if user_id not in name_cache[server_id]:
                d2n = load_discord2name(server_id)
                name_cache[server_id][user_id] = d2n.get(user_id, user_name)
            user_name = name_cache[server_id][user_id]
            user_names.append(user_name)
        user_names_str = ", ".join(user_names)

        description = f"{option}"
        if reaction_count > 0:
            description = (
                f"**{option} : {reaction_count}/{participant_count} votes\n({user_names_str})**"
                if emoji_str in max_reaction_indices
                else f"{option} : **{reaction_count}/{participant_count} votes**\n({user_names_str})"
            )

        description_list.append(description)

    embed.description = "\n\n".join(description_list)
    return embed


__all__ = ["CustomPaginator", "format_poll", "name_cache"]
