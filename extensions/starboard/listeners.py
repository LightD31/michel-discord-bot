"""Reaction listeners that mirror popular messages to the configured starboard."""

from datetime import datetime

from interactions import Embed, listen
from interactions.api.events import MessageReactionAdd, MessageReactionRemove

from features.starboard import StarEntry
from src.discord_ext.embeds import Colors

from ._common import get_guild_settings, logger


def _jump_url(guild_id: str | int, channel_id: str | int, message_id: str | int) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _first_image_url(message) -> str | None:
    """Return the URL of the first image attachment or embed image, if any."""
    for att in getattr(message, "attachments", []) or []:
        ctype = (getattr(att, "content_type", None) or "").lower()
        if ctype.startswith("image/"):
            return att.url
        # Fallback by extension if content_type is missing.
        url = getattr(att, "url", "") or ""
        if url.lower().rsplit("?", 1)[0].endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return url
    for embed in getattr(message, "embeds", []) or []:
        img = getattr(embed, "image", None)
        if img and getattr(img, "url", None):
            return img.url
        thumb = getattr(embed, "thumbnail", None)
        if thumb and getattr(thumb, "url", None):
            return thumb.url
    return None


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
    image_url = _first_image_url(message)
    if image_url:
        embed.set_image(url=image_url)
    embed.add_field(
        name="Source",
        value=f"[Aller au message]({_jump_url(message.guild.id, message.channel.id, message.id)})",
        inline=False,
    )
    embed.set_footer(text=f"{emoji} {count} • #{getattr(message.channel, 'name', message.channel.id)}")
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
        if str(reaction.emoji) != emoji_target:
            continue
        try:
            users = await reaction.users().flatten()
        except Exception as e:
            logger.debug("Could not list reaction users: %s", e)
            return reaction.count
        valid = []
        for u in users:
            if ignore_bots and getattr(u, "bot", False):
                continue
            if not allow_self and u.id == message.author.id:
                continue
            valid.append(u)
        return len(valid)
    return 0


class ListenersMixin:
    """React to ⭐ adds/removes and keep the starboard mirror in sync."""

    @listen(MessageReactionAdd)
    async def on_reaction_add(self, event: MessageReactionAdd) -> None:
        await self._handle_reaction(event)

    @listen(MessageReactionRemove)
    async def on_reaction_remove(self, event: MessageReactionRemove) -> None:
        await self._handle_reaction(event)

    async def _handle_reaction(
        self, event: MessageReactionAdd | MessageReactionRemove
    ) -> None:
        message = event.message
        if message is None or message.guild is None:
            return

        settings = get_guild_settings(message.guild.id)
        if not settings:
            return

        emoji = settings.get("emoji", "⭐") or "⭐"
        if str(event.emoji) != emoji:
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

        threshold = max(1, int(settings.get("threshold", 3) or 3))
        allow_self = bool(settings.get("allowSelfStar", False))

        async with self.lock:
            await self._sync_mirror(
                message=message,
                guild_id=str(message.guild.id),
                emoji=emoji,
                threshold=threshold,
                allow_self=allow_self,
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
            message,
            emoji,
            allow_self=allow_self,
            ignore_bots=ignore_bots,
        )

        if count >= threshold and existing is None:
            try:
                channel = await self.bot.fetch_channel(int(starboard_channel_id))
            except Exception as e:
                logger.error("Cannot fetch starboard channel %s: %s", starboard_channel_id, e)
                return
            if channel is None or not hasattr(channel, "send"):
                return

            embed = _build_embed(message, count, emoji)
            try:
                sent = await channel.send(embeds=[embed])  # type: ignore[union-attr]
            except Exception as e:
                logger.error("Failed to publish to starboard: %s", e)
                return

            entry = StarEntry(
                guild_id=guild_id,
                channel_id=str(message.channel.id),
                original_message_id=str(message.id),
                mirror_channel_id=str(channel.id),
                mirror_message_id=str(sent.id),
                author_id=str(message.author.id),
                count=count,
                created_at=datetime.now(),
            )
            await repo.upsert(entry)
            logger.info(
                "Starboard: posted message %s (count=%d) in guild %s", message.id, count, guild_id
            )
            return

        if existing is not None and count >= threshold:
            if existing.count == count:
                return
            try:
                channel = await self.bot.fetch_channel(int(existing.mirror_channel_id))
                if channel and hasattr(channel, "fetch_message"):
                    msg = await channel.fetch_message(int(existing.mirror_message_id))
                    if msg:
                        embed = _build_embed(message, count, emoji)
                        await msg.edit(embeds=[embed])
            except Exception as e:
                logger.warning("Could not refresh starboard mirror: %s", e)
            await repo.update_count(str(message.id), count)
            return

        if existing is not None and count < threshold:
            if remove_below_threshold:
                try:
                    channel = await self.bot.fetch_channel(int(existing.mirror_channel_id))
                    if channel and hasattr(channel, "fetch_message"):
                        msg = await channel.fetch_message(int(existing.mirror_message_id))
                        if msg:
                            await msg.delete()
                except Exception as e:
                    logger.warning("Could not delete starboard mirror: %s", e)
                await repo.delete_by_original(str(message.id))
                logger.info("Starboard: removed message %s (count fell to %d)", message.id, count)
            else:
                # Keep the mirror but refresh the count footer.
                try:
                    channel = await self.bot.fetch_channel(int(existing.mirror_channel_id))
                    if channel and hasattr(channel, "fetch_message"):
                        msg = await channel.fetch_message(int(existing.mirror_message_id))
                        if msg:
                            embed = _build_embed(message, count, emoji)
                            await msg.edit(embeds=[embed])
                except Exception as e:
                    logger.warning("Could not refresh starboard mirror: %s", e)
                await repo.update_count(str(message.id), count)
