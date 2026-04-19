"""Embed builders and notification senders for the Twitch extension."""

import os
from datetime import datetime

import pytz
from interactions import Embed, EmbedFooter
from twitchAPI.helper import first
from twitchAPI.object.api import ChannelInformation, Stream, TwitchUser
from twitchAPI.object.eventsub import ChannelUpdateEvent

from src.core import logging as logutil
from src.discord_ext.embeds import Colors

logger = logutil.init_logger(os.path.basename(__file__))

DEFAULT_EMBED_COLOR = Colors.TWITCH


class NotificationsMixin:
    """Embed construction and stream-state notification delivery."""

    @staticmethod
    def get_display_value(value: str | None, fallback: str = "Non renseigné") -> str:
        """Normalize optional values before displaying them in notifications."""
        if value is None:
            return fallback
        normalized = str(value).strip()
        return normalized if normalized else fallback

    async def create_notification_embed(
        self,
        guild_id: int,
        title: str,
        description: str,
        color: int = DEFAULT_EMBED_COLOR,
    ) -> Embed:
        """Create a notification embed with a uniform footer/timestamp layout."""
        now = datetime.now(pytz.UTC)
        try:
            bot = await self.bot.fetch_member(self.bot.user.id, guild_id)
            return Embed(
                title=title,
                description=description,
                color=color,
                timestamp=now,
                footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
            )
        except Exception as e:
            logger.error("Error creating notification embed for guild %s: %s", guild_id, e)
            return Embed(
                title=title,
                description=description,
                color=color,
                timestamp=now,
            )

    async def create_stream_embed(
        self,
        stream: Stream | None,
        user_id: str,
        offline: bool = False,
        data: ChannelUpdateEvent | None = None,
    ) -> Embed:
        user_id, title, description, user_login = await self.get_stream_info(
            stream, user_id, offline, data
        )
        embed = await self.create_embed(title, description, user_login)
        if stream:
            embed.set_image(
                url=f"{stream.thumbnail_url.format(width=1280, height=720)}?{datetime.now().timestamp()}"
            )
        await self.add_user(embed, user_id, offline)
        return embed

    async def get_stream_info(
        self,
        stream: Stream | None,
        user_id: str,
        offline: bool,
        data: ChannelUpdateEvent | None = None,
    ) -> tuple:
        if data:
            return self.get_stream_info_from_data(data, offline, stream)
        if offline:
            return await self.get_offline_stream_info(user_id)
        return self.get_online_stream_info(stream)

    def get_stream_info_from_data(
        self,
        data: ChannelUpdateEvent,
        offline: bool,
        stream: Stream | None = None,
    ) -> tuple:
        """Extract user, title, description, login from a ChannelUpdateEvent."""
        user_id = data.event.broadcaster_user_id
        user_login = data.event.broadcaster_user_name
        title = data.event.title
        game = data.event.category_name
        description = (
            f"Ne joue pas à **{game}**"
            if offline
            else f"Joue à **{data.event.category_name}** pour **{stream.viewer_count}** viewers"
        )
        return user_id, title, description, user_login

    async def get_offline_stream_info(self, user_id: str) -> tuple:
        """Fetch title/game via the Twitch API when the channel is offline."""
        channel_infos = await self.twitch.get_channel_information(user_id)
        channel: ChannelInformation = channel_infos[0] if channel_infos else None
        game = channel.game_name
        description = f"Ne joue pas à **{game}**"
        title = channel.title
        user_login = channel.broadcaster_login
        return user_id, title, description, user_login

    def get_online_stream_info(self, stream: Stream) -> tuple:
        """Extract title/game/viewer count from a live Stream object."""
        description = f"Joue à **{stream.game_name}** pour **{stream.viewer_count}** viewers"
        title = stream.title
        user_login = stream.user_login
        return stream.user_id, title, description, user_login

    async def create_embed(
        self,
        title: str,
        description: str,
        user_login: str,
        guild_id: int | None = None,
    ) -> Embed:
        """Create a Twitch-branded embed linking to the streamer channel."""
        if guild_id is None:
            if self.enabled_servers:
                guild_id = int(self.enabled_servers[0])
            else:
                return Embed(
                    title=title,
                    description=description,
                    color=Colors.TWITCH,
                    url=f"https://twitch.tv/{user_login}",
                    timestamp=datetime.now(pytz.UTC),
                )

        try:
            bot = await self.bot.fetch_member(self.bot.user.id, guild_id)
            return Embed(
                title=title,
                description=description,
                color=Colors.TWITCH,
                url=f"https://twitch.tv/{user_login}",
                footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
                timestamp=datetime.now(pytz.UTC),
            )
        except Exception as e:
            logger.error(f"Error creating embed for {user_login} in guild {guild_id}: {e}")
            return Embed(
                title=title,
                description=description,
                color=Colors.TWITCH,
                url=f"https://twitch.tv/{user_login}",
                timestamp=datetime.now(pytz.UTC),
            )

    async def add_user(self, embed: Embed, user_id: str, offline: bool = False) -> Embed:
        """Attach the broadcaster's author/avatar (and offline image) to the embed."""
        try:
            user: TwitchUser = await first(self.twitch.get_users(user_ids=[user_id]))
            status = "n'est pas en live" if offline else "est en live !"
            embed.set_author(
                name=f"{user.display_name} {status}",
                icon_url=user.profile_image_url,
                url=f"https://twitch.tv/{user.login}",
            )

            if offline and hasattr(user, "offline_image_url") and user.offline_image_url:
                embed.set_image(url=f"{user.offline_image_url}?{datetime.now().timestamp()}")

            return embed
        except Exception as e:
            logger.error(f"Error adding user {user_id} to embed: {e}")
            return embed

    async def send_stream_start_notification(
        self,
        streamer,
        broadcaster_name: str,
        stream: Stream | None,
    ) -> None:
        """Send a notification message when a stream starts."""
        if not streamer.notif_channel:
            return
        if not streamer.notify_stream_start:
            return

        try:
            title = self.get_display_value(stream.title if stream else None, "Titre non renseigné")
            category = self.get_display_value(
                stream.game_name if stream else None, "Catégorie non renseignée"
            )

            embed = await self.create_notification_embed(
                streamer.guild_id,
                title=f"{broadcaster_name} est en live !",
                description=f"[Rejoindre le stream](https://twitch.tv/{streamer.streamer_id})",
                color=Colors.TWITCH,
            )
            embed.add_field(name="Titre", value=title, inline=False)
            embed.add_field(name="Catégorie", value=category, inline=True)
            if stream and stream.viewer_count is not None:
                embed.add_field(name="Viewers", value=str(stream.viewer_count), inline=True)

            if stream and stream.thumbnail_url:
                embed.set_image(
                    url=f"{stream.thumbnail_url.format(width=1280, height=720)}?{datetime.now().timestamp()}"
                )

            try:
                user: TwitchUser = await first(self.twitch.get_users(user_ids=[streamer.user_id]))
                if user:
                    embed.set_thumbnail(url=user.profile_image_url)
            except Exception as e:
                logger.debug(f"Could not fetch user info for thumbnail: {e}")

            await streamer.notif_channel.send(embed=embed)
            logger.info(
                "Sent stream start notification for %s in guild %s",
                streamer.streamer_id,
                streamer.guild_id,
            )
        except Exception as e:
            logger.error(
                "Error sending stream start notification for %s: %s",
                streamer.streamer_id,
                e,
            )

    async def send_stream_end_notification(self, streamer, broadcaster_name: str) -> None:
        """Send a notification message when a stream ends with a session summary."""
        if not streamer.notif_channel:
            return
        if not streamer.notify_stream_end:
            return

        try:
            end_time = datetime.now(pytz.UTC)
            duration_str = "Durée inconnue"

            if streamer.stream_start_time:
                duration = end_time - streamer.stream_start_time
                hours, remainder = divmod(int(duration.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)

                if hours > 0:
                    duration_str = f"{hours}h {minutes}min"
                else:
                    duration_str = f"{minutes}min {seconds}s"

            vod_url = None
            try:
                videos = self.twitch.get_videos(
                    user_id=streamer.user_id, video_type="archive", first=1
                )
                async for video in videos:
                    if streamer.stream_id and video.stream_id == streamer.stream_id:
                        vod_url = video.url
                    break
            except Exception as e:
                logger.debug(f"Could not fetch VOD for {streamer.streamer_id}: {e}")

            title = self.get_display_value(streamer.stream_title, "Titre non renseigné")

            categories: list[str] = []
            for cat in streamer.stream_categories:
                if cat and (not categories or categories[-1] != cat):
                    categories.append(cat)

            if len(categories) > 1:
                category_label = "Catégories"
                category_value = "\n".join(f"• {c}" for c in categories)
            else:
                category_label = "Catégorie"
                category_value = categories[0] if categories else "Catégorie non renseignée"

            embed = await self.create_notification_embed(
                streamer.guild_id,
                title=f"{broadcaster_name} a terminé son live",
                description="Résumé de la session Twitch",
                color=Colors.TWITCH_ALT,
            )

            embed.add_field(name="Durée", value=duration_str, inline=True)
            embed.add_field(name="Titre", value=title, inline=False)
            embed.add_field(name=category_label, value=category_value, inline=False)

            if vod_url:
                embed.add_field(name="VOD", value=f"[Regarder le replay]({vod_url})", inline=False)

            try:
                user: TwitchUser = await first(self.twitch.get_users(user_ids=[streamer.user_id]))
                if user:
                    embed.set_thumbnail(url=user.profile_image_url)
            except Exception as e:
                logger.debug(f"Could not fetch user info for thumbnail: {e}")

            await streamer.notif_channel.send(embed=embed)
            logger.info(
                f"Sent stream end notification for {streamer.streamer_id} in guild {streamer.guild_id}"
            )

        except Exception as e:
            logger.error(f"Error sending stream end notification for {streamer.streamer_id}: {e}")
