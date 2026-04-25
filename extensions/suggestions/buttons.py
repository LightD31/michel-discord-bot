"""Persistent vote button — toggle/replace the user's up/down vote."""

import re

from interactions import ComponentContext, component_callback

from src.discord_ext.messages import send_error

from ._common import VOTE_PREFIX, get_guild_settings, logger

_CUSTOM_ID_RE = re.compile(rf"^{VOTE_PREFIX}:(\d+):(up|down)$")


class ButtonsMixin:
    """Handle 👍 / 👎 clicks on suggestion embeds."""

    @component_callback(_CUSTOM_ID_RE)
    async def on_vote_button(self, ctx: ComponentContext) -> None:
        if not ctx.guild:
            await send_error(ctx, "Cette action n'est disponible qu'en serveur.")
            return

        match = _CUSTOM_ID_RE.match(ctx.custom_id)
        if not match:
            return
        sugg_id, direction = int(match.group(1)), match.group(2)

        settings = get_guild_settings(ctx.guild_id)
        if not settings:
            await send_error(ctx, "Le module suggestions n'est plus actif.")
            return

        repo = self.repository(ctx.guild_id)
        existing = await repo.get(sugg_id)
        if not existing:
            await send_error(ctx, "Cette suggestion n'existe plus.")
            return
        if existing.status != "pending":
            await send_error(ctx, "Le vote est clos pour cette suggestion.")
            return

        user_id = str(ctx.author.id)
        previous = existing.votes.get(user_id)
        if previous == direction:
            updated = await repo.remove_vote(sugg_id, user_id)
            action_text = "Vote retiré"
        else:
            updated = await repo.set_vote(sugg_id, user_id, direction)
            action_text = "Vote 👍 enregistré" if direction == "up" else "Vote 👎 enregistré"

        if not updated:
            await send_error(ctx, "Impossible d'enregistrer le vote.")
            return

        # Update the original embed's Votes field in place.
        try:
            up, down = updated.tally()
            embed = ctx.message.embeds[0]
            for field in embed.fields or []:
                if field.name == "Votes":
                    field.value = f"👍 {up} · 👎 {down}"
                    break
            await ctx.message.edit(embeds=[embed])
        except Exception as e:
            logger.warning("Could not refresh suggestion #%s embed: %s", sugg_id, e)

        await ctx.send(action_text, ephemeral=True)
