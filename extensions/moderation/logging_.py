"""Centralized modlog embed posting + best-effort DM helpers (ModLogMixin)."""

import contextlib
from typing import Any

from interactions import Embed

from features.moderation import Infraction, humanize_duration
from src.discord_ext.embeds import Colors

from ._common import AUTOMOD_MODERATOR_ID, TYPE_COLORS, TYPE_LABELS, logger


def build_case_embed(
    infraction: Infraction,
    *,
    target_name: str | None = None,
    moderator_name: str | None = None,
) -> Embed:
    """Build the standardized modlog embed for a single case."""
    embed = Embed(
        title=f"{TYPE_LABELS.get(infraction.type, infraction.type)} · Cas #{infraction.id}",
        color=TYPE_COLORS.get(infraction.type, Colors.UTIL),
    )
    target_value = target_name or f"<@{infraction.user_id}>"
    embed.add_field(name="Membre", value=f"{target_value} (`{infraction.user_id}`)", inline=True)

    if infraction.moderator_id == AUTOMOD_MODERATOR_ID:
        mod_value = "🛡️ Automod"
    else:
        mod_value = moderator_name or f"<@{infraction.moderator_id}>"
    embed.add_field(name="Modérateur", value=mod_value, inline=True)

    if infraction.duration_seconds:
        embed.add_field(
            name="Durée", value=humanize_duration(infraction.duration_seconds), inline=True
        )
    embed.add_field(name="Raison", value=(infraction.reason or "*Aucune*")[:1024], inline=False)
    if not infraction.active:
        embed.add_field(name="Statut", value="❌ Révoqué", inline=True)
    embed.timestamp = infraction.created_at
    return embed


class ModLogMixin:
    """Posts standardized case embeds to the modlog channel + DMs members."""

    bot: Any

    async def log_case(
        self,
        settings: dict,
        infraction: Infraction,
        *,
        target_name: str | None = None,
        moderator_name: str | None = None,
    ) -> None:
        """Post *infraction* to the configured modlog channel (best effort)."""
        channel_id = settings.get("modLogChannelId")
        if not channel_id:
            return
        try:
            channel = await self.bot.fetch_channel(int(channel_id))
        except Exception as e:
            logger.warning("Modlog channel %s unreachable: %s", channel_id, e)
            return
        if channel is None or not hasattr(channel, "send"):
            logger.warning("Modlog channel %s is invalid", channel_id)
            return
        embed = build_case_embed(infraction, target_name=target_name, moderator_name=moderator_name)
        try:
            await channel.send(embeds=[embed])
        except Exception as e:
            logger.warning("Failed to post modlog case #%s: %s", infraction.id, e)

    async def dm_target(self, settings: dict, target: Any, text: str) -> None:
        """DM *target* when ``dmOnAction`` is enabled; swallow closed-DM errors."""
        if not settings.get("dmOnAction", True) or target is None:
            return
        # Closed DMs, bot account, or left the guild — non-fatal.
        with contextlib.suppress(Exception):
            await target.send(text)
