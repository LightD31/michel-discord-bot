"""Reaction listeners that mirror popular messages to the configured starboard."""

from datetime import datetime

from interactions import Embed, listen
from interactions.api.events import MessageReactionAdd, MessageReactionRemove

from features.starboard import (
    MirrorAction,
    StarEntry,
    count_valid_reactors,
    emoji_matches,
    first_image_url,
    plan_mirror,
)
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import edit_message_if_changed

from ._common import get_guild_settings, logger


def _jump_url(guild_id: str | int, channel_id: str | int, message_id: str | int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _build_embed(message, count: int, emoji: str) -> Embed:
    author = message.author
    author_name = getattr(author, "display_name", None) or getattr(author, "username", "?")
    avatar_url = None
    avatar = getattr(author, "avatar_url", None)
    if avatar:
        avatar_url = avatar if isinstance(avatar, str) else getattr(avatar, "url", None)

    content = (message.content or "").strip()
    description = content if content else "*(message sans texte)*"

    embed = Embed(description=description, color=Colors.WARNING)
    embed.set_author(name=author_name, icon_url=avatar_url)
    image_url = first_image_url(message)
    if image_url:
        embed.set_image(url=image_url)
    embed.add_field(
        name="Source",
        value=f"[Aller au message]({_jump_url(message.guild.id, message.channel.id, message.id)})",
        inline=False,
    )
    embed.set_footer(
        text=f"{emoji} {count} • #{getattr(message.channel, 'name', message.channel.id)}"
    )
    embed.timestamp = getattr(message, "created_at", None) or datetime.now()
    return embed


async def _count_valid_reactions(
    message,
    emoji_target: str,
    *,
    allow_self: bool,
    ignore_bots: bool,
) -> int:
    """Return the number of users whose reaction with this emoji counts toward the threshold."""
    for reaction in getattr(message, "reactions", []) or []:
        if not emoji_matches(str(reaction.emoji), emoji_target):
            continue
        try:
            users = await reaction.users().flatten()
        except Exception as e:
            logger.debug("Could not list reaction users: %s", e)
            return reaction.count
        return count_valid_reactors(
            users, author_id=message.author.id, allow_self=allow_self, ignore_bots=ignore_bots
        )
    return 0


class ListenersMixin:
    """React to ⭐ adds/removes and keep the starboard mirror in sync."""

    @listen(MessageReactionAdd)
    async def on_reaction_add(self, event: MessageReactionAdd) -> None:
        await self._handle_reaction(event)

    @listen(MessageReactionRemove)
    async def on_reaction_remove(self, event: MessageReactionRemove) -> None:
        await self._handle_reaction(event)

    async def _handle_reaction(self, event: MessageReactionAdd | MessageReactionRemove) -> None:
        message = event.message
        if message is None or message.guild is None:
            return

        settings = get_guild_settings(message.guild.id)
        if not settings:
            return

        emoji = settings.get("emoji", "⭐") or "⭐"
        if not emoji_matches(str(event.emoji), emoji):
            return

        starboard_channel_id = settings.get("starboardChannelId")
        if not starboard_channel_id:
            return
        if str(message.channel.id) == str(starboard_channel_id):
            return

        ignored = {str(c) for c in (settings.get("ignoredChannels") or []) if c}
        if str(message.channel.id) in ignored:
            return

        ignore_bots = bool(settings.get("ignoreBots", True))
        if ignore_bots and getattr(message.author, "bot", False):
            return

        async with self.lock:
            await self._sync_mirror(
                message=message,
                guild_id=str(message.guild.id),
                emoji=emoji,
                threshold=max(1, int(settings.get("threshold", 3) or 3)),
                allow_self=bool(settings.get("allowSelfStar", False)),
                ignore_bots=ignore_bots,
                starboard_channel_id=str(starboard_channel_id),
                remove_below_threshold=bool(settings.get("removeBelowThreshold", False)),
            )

    async def _sync_mirror(
        self,
        *,
        message,
        guild_id: str,
        emoji: str,
        threshold: int,
        allow_self: bool,
        ignore_bots: bool,
        starboard_channel_id: str,
        remove_below_threshold: bool,
    ) -> None:
        repo = self.repository(guild_id)
        existing = await repo.get_by_original(str(message.id))
        count = await _count_valid_reactions(
            message, emoji, allow_self=allow_self, ignore_bots=ignore_bots
        )
        action = plan_mirror(
            existing.count if existing else None,
            count,
            threshold,
            remove_below=remove_below_threshold,
        )

        if action is MirrorAction.POST:
            await self._post_mirror(message, guild_id, count, emoji, starboard_channel_id)
        elif existing is None or action is MirrorAction.NOTHING:
            return
        elif action is MirrorAction.REMOVE:
            mirror = await self._fetch_mirror(existing)
            if mirror is not None:
                try:
                    await mirror.delete()
                except Exception as e:
                    logger.warning("Could not delete starboard mirror: %s", e)
            await repo.delete_by_original(str(message.id))
            logger.info("Starboard: removed message %s (count fell to %d)", message.id, count)
        else:  # REFRESH
            mirror = await self._fetch_mirror(existing)
            if mirror is not None:
                embed = _build_embed(message, count, emoji)
                await edit_message_if_changed(mirror, embeds=[embed], logger=logger)
            await repo.update_count(str(message.id), count)

    async def _post_mirror(
        self, message, guild_id: str, count: int, emoji: str, starboard_channel_id: str
    ) -> None:
        try:
            channel = await self.bot.fetch_channel(int(starboard_channel_id))
        except Exception as e:
            logger.error("Cannot fetch starboard channel %s: %s", starboard_channel_id, e)
            return
        if channel is None or not hasattr(channel, "send"):
            return
        try:
            sent = await channel.send(embeds=[_build_embed(message, count, emoji)])  # type: ignore[union-attr]
        except Exception as e:
            logger.error("Failed to publish to starboard: %s", e)
            return
        await self.repository(guild_id).upsert(
            StarEntry(
                guild_id=guild_id,
                channel_id=str(message.channel.id),
                original_message_id=str(message.id),
                mirror_channel_id=str(channel.id),
                mirror_message_id=str(sent.id),
                author_id=str(message.author.id),
                count=count,
                created_at=datetime.now(),
            )
        )
        logger.info(
            "Starboard: posted message %s (count=%d) in guild %s", message.id, count, guild_id
        )

    async def _fetch_mirror(self, entry: StarEntry):
        """The mirror message on the starboard, or ``None`` if it can't be fetched."""
        try:
            channel = await self.bot.fetch_channel(int(entry.mirror_channel_id))
            if channel and hasattr(channel, "fetch_message"):
                return await channel.fetch_message(int(entry.mirror_message_id))
        except Exception as e:
            logger.warning("Could not fetch starboard mirror %s: %s", entry.mirror_message_id, e)
        return None
