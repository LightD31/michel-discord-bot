"""Persistent vote button — toggle/replace the user's up/down vote."""

import re
from typing import TYPE_CHECKING, Literal

from interactions import ComponentContext, component_callback

from features.suggestions import SuggestionsRepository
from src.discord_ext.messages import (
    copy_embed,
    edit_message_if_changed,
    guild_id_of,
    message_of,
    send_error,
)

from ._common import VOTE_PREFIX, get_guild_settings, logger

_CUSTOM_ID_RE = re.compile(rf"^{VOTE_PREFIX}:(\d+):(up|down)$")


class ButtonsMixin:
    """Handle 👍 / 👎 clicks on suggestion embeds."""

    if TYPE_CHECKING:

        def repository(self, guild_id: str | int) -> SuggestionsRepository: ...

    @component_callback(_CUSTOM_ID_RE)
    async def on_vote_button(self, ctx: ComponentContext) -> None:
        match = _CUSTOM_ID_RE.match(ctx.custom_id)
        if not match:
            return
        sugg_id = int(match.group(1))
        direction: Literal["up", "down"] = "up" if match.group(2) == "up" else "down"

        settings = get_guild_settings(guild_id_of(ctx))
        if not settings:
            await send_error(ctx, "Le module suggestions n'est plus actif.")
            return

        repo = self.repository(guild_id_of(ctx))
        existing = await repo.get(sugg_id)
        if not existing:
            await send_error(ctx, "Cette suggestion n'existe plus.")
            return
        if existing.status != "pending":
            await send_error(ctx, "Le vote est clos pour cette suggestion.")
            return

        user_id = str(ctx.author.id)
        updated = await repo.toggle_vote(sugg_id, user_id, direction)
        if not updated:
            await send_error(ctx, "Le vote est clos pour cette suggestion.")
            return
        if user_id not in updated.votes:
            action_text = "Vote retiré"
        else:
            action_text = "Vote 👍 enregistré" if direction == "up" else "Vote 👎 enregistré"

        # Update the original embed's Votes field in place.
        try:
            up, down = updated.tally()
            embed = copy_embed(message_of(ctx).embeds[0])
            for field in embed.fields or []:
                if field.name == "Votes":
                    field.value = f"👍 {up} · 👎 {down}"
                    break
            await edit_message_if_changed(message_of(ctx), embeds=[embed], logger=logger)
        except Exception as e:
            logger.warning("Could not refresh suggestion #%s embed: %s", sugg_id, e)

        await ctx.send(action_text, ephemeral=True)
