"""Reaction listeners that mirror popular messages to the configured starboard.

Staff can also put a message there by hand: right-click → Applications →
"⭐ Mettre au starboard" (``Gérer les messages`` in the message's channel), with
the same NSFW and private-channel rules as a message the reactions bring.
"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from interactions import (
    Client,
    ContextMenuContext,
    Embed,
    Permissions,
    Timestamp,
    listen,
    message_context_menu,
)
from interactions.api.events import MessageReactionAdd, MessageReactionRemove

from features.starboard import (
    MirrorAction,
    StarboardRepository,
    StarEntry,
    count_valid_reactors,
    emoji_matches,
    exposes_hidden_channel,
    first_image_url,
    plan_mirror,
)
from src.core import clock
from src.core.logging import log_failure
from src.discord_ext.channels import everyone_can_view, fetch_guild_channel
from src.discord_ext.embeds import Colors
from src.discord_ext.menus import menu_help
from src.discord_ext.messages import (
    CAN_PIN,
    author_can,
    edit_message_if_changed,
    guild_id_of,
    jump_url,
    send_error,
)
from src.discord_ext.scopes import guild_scopes

from ._common import STARBOARD, get_guild_settings, logger

STAR_MENU = "⭐ Mettre au starboard"
Posted = Literal["posted", "no_board", "nsfw", "private", "failed"]
NOT_POSTED: dict[str, str] = {
    "no_board": (
        "Le salon du starboard est introuvable : un administrateur doit le choisir à nouveau "
        "dans le tableau de bord."
    ),
    "nsfw": "Ce message vient d'un salon NSFW et le starboard ne l'est pas : il reste ici.",
    "private": (
        "Ce salon n'est pas visible de tous, le starboard si : le message reste ici. Un "
        "administrateur peut l'autoriser (« Accepter les salons privés »)."
    ),
    "failed": "Je n'ai pas pu publier au starboard : vérifie mes permissions dans son salon.",
}


def _build_embed(message, count: int, emoji: str, guild_id: str | int | None = None) -> Embed:
    author = message.author
    author_name = str(getattr(author, "display_name", None) or getattr(author, "username", "?"))
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
        value=(
            f"[Aller au message]({jump_url(guild_id or message.guild.id, message.channel.id, message.id)})"
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"{emoji} {count} • #{getattr(message.channel, 'name', message.channel.id)}"
    )
    embed.timestamp = Timestamp.fromdatetime(getattr(message, "created_at", None) or clock.now())
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

    # Provided by StarboardExtension (declared for the type checker only).
    bot: Client
    lock: asyncio.Lock

    if TYPE_CHECKING:

        def repository(self, guild_id: str | int) -> StarboardRepository: ...

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
                allow_private=bool(settings.get("allowPrivateSources", False)),
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
        allow_private: bool = False,
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
            await self._post_mirror(
                message, guild_id, count, emoji, starboard_channel_id, allow_private=allow_private
            )
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
                embed = _build_embed(message, count, emoji, guild_id)
                await edit_message_if_changed(mirror, embeds=[embed], logger=logger)
            await repo.update_count(str(message.id), count)

    async def _post_mirror(
        self,
        message,
        guild_id: str,
        count: int,
        emoji: str,
        starboard_channel_id: str,
        *,
        allow_private: bool = False,
    ) -> Posted:
        channel = await fetch_guild_channel(self.bot, guild_id, starboard_channel_id, logger=logger)
        if channel is None or not hasattr(channel, "send"):
            return "no_board"
        # Starring copies the message into the starboard: content from an
        # age-restricted channel must not land in one that isn't (a thread
        # reports its parent's setting).
        if getattr(message.channel, "nsfw", False) and not getattr(channel, "nsfw", False):
            logger.info("Starboard: message %s is in an NSFW channel, not mirrored", message.id)
            return "nsfw"
        if exposes_hidden_channel(
            source_public=everyone_can_view(message.channel),
            board_public=everyone_can_view(channel),
            allow_private=allow_private,
        ):
            logger.info("Starboard: message %s is in a private channel, not mirrored", message.id)
            return "private"
        try:
            sent = await channel.send(embeds=[_build_embed(message, count, emoji, guild_id)])
        except Exception as e:
            log_failure(logger, "Failed to publish to starboard: %s", e)
            return "failed"
        await self.repository(guild_id).upsert(
            StarEntry(
                guild_id=guild_id,
                channel_id=str(message.channel.id),
                original_message_id=str(message.id),
                mirror_channel_id=str(channel.id),
                mirror_message_id=str(sent.id),
                author_id=str(message.author.id),
                count=count,
                created_at=clock.now(),
            )
        )
        logger.info(
            "Starboard: posted message %s (count=%d) in guild %s", message.id, count, guild_id
        )
        return "posted"

    @menu_help("Publier ce message au starboard sans attendre les réactions (staff)")
    @message_context_menu(
        name=STAR_MENU,
        scopes=guild_scopes(STARBOARD.guild_ids()),
        default_member_permissions=Permissions.MANAGE_MESSAGES,
    )
    async def star_menu(self, ctx: ContextMenuContext) -> None:
        message: Any = ctx.target
        guild_id = guild_id_of(ctx)
        settings = get_guild_settings(guild_id) or {}
        board_id = str(settings.get("starboardChannelId") or "")
        if not board_id:
            await send_error(ctx, NOT_POSTED["no_board"], icon="⭐")
            return
        # The default permission is only checked where the menu is used: here,
        # the message's own channel, but a server can loosen it.
        if not await author_can(ctx, ctx.channel, CAN_PIN):
            return
        if str(ctx.channel_id) == board_id:
            await send_error(ctx, "Ce message est déjà dans le salon du starboard.", icon="⭐")
            return
        repo = self.repository(guild_id)
        emoji = settings.get("emoji", "⭐") or "⭐"
        async with self.lock:
            existing = await repo.get_by_original(str(message.id))
            if existing is not None:
                link = jump_url(guild_id, existing.mirror_channel_id, existing.mirror_message_id)
                await send_error(ctx, f"Déjà au starboard : [voir]({link}).", icon="⭐")
                return
            count = await _count_valid_reactions(
                message,
                emoji,
                allow_self=bool(settings.get("allowSelfStar", False)),
                ignore_bots=bool(settings.get("ignoreBots", True)),
            )
            outcome = await self._post_mirror(
                message,
                guild_id,
                count,
                emoji,
                board_id,
                allow_private=bool(settings.get("allowPrivateSources", False)),
            )
        if outcome != "posted":
            await send_error(ctx, NOT_POSTED[outcome])
            return
        logger.info("Starboard: message %s put there by %s", message.id, ctx.author.id)
        await ctx.send(f"⭐ Message mis au starboard, dans <#{board_id}>.", ephemeral=True)

    async def _fetch_mirror(self, entry: StarEntry):
        """The mirror message on the starboard, or ``None`` if it can't be fetched."""
        try:
            channel = await fetch_guild_channel(self.bot, entry.guild_id, entry.mirror_channel_id)
            if channel and hasattr(channel, "fetch_message"):
                return await channel.fetch_message(int(entry.mirror_message_id))
        except Exception as e:
            logger.warning("Could not fetch starboard mirror %s: %s", entry.mirror_message_id, e)
        return None
