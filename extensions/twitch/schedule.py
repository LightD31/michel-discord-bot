"""Twitch schedule embed + planning message + Discord scheduled-event sync."""

import os
from datetime import datetime, timedelta

import pytz
from interactions import (
    Embed,
    EmbedFooter,
    Guild,
    OrTrigger,
    ScheduledEventStatus,
    ScheduledEventType,
    Task,
    TimestampStyles,
    TimeTrigger,
    utils,
)
from twitchAPI.object.api import ChannelStreamSchedule, ChannelStreamScheduleSegment
from twitchAPI.object.eventsub import ChannelUpdateEvent, StreamOfflineEvent
from twitchAPI.type import TwitchResourceNotFound

from src.core import logging as logutil
from src.discord_ext.embeds import Colors

from ._common import StreamerInfo, ensure_utc

logger = logutil.init_logger(os.path.basename(__file__))


class ScheduleMixin:
    """Fetches Twitch schedules, updates the planning message, and manages the
    per-streamer Discord scheduled event.
    """

    def add_field_to_embed(
        self,
        embed: Embed,
        stream: ChannelStreamScheduleSegment,
        is_now: bool = False,
    ) -> None:
        now = datetime.now(pytz.UTC)
        start_time = ensure_utc(stream.start_time)
        end_time = ensure_utc(stream.end_time)
        title = stream.title if stream.title is not None else "Pas de titre défini"
        category = (
            stream.category.name if stream.category is not None else "Pas de catégorie définie"
        )
        if is_now:
            name = (
                "<:live_1:1265285043891343391><:live_2:1265285055186468864>"
                f"<:live_3:1265285063818477703> {title}"
            )
            value = (
                f"**{category}\nEn cours "
                f"({str(utils.timestamp_converter(start_time).format(TimestampStyles.ShortTime))}"
                f"-{str(utils.timestamp_converter(end_time).format(TimestampStyles.ShortTime))})\n\u200b**"
            )
        elif start_time < now + timedelta(days=2):
            name = f"{title}"
            value = (
                f"{category}\n"
                f"{utils.timestamp_converter(start_time).format(TimestampStyles.RelativeTime)} "
                f"({str(utils.timestamp_converter(start_time).format(TimestampStyles.ShortTime))}"
                f"-{str(utils.timestamp_converter(end_time).format(TimestampStyles.ShortTime))})\n\u200b"
            )
        else:
            name = f"{title}"
            value = (
                f"{category}\n"
                f"{utils.timestamp_converter(start_time).format(TimestampStyles.LongDate)} "
                f"({str(utils.timestamp_converter(start_time).format(TimestampStyles.ShortTime))}"
                f"-{str(utils.timestamp_converter(end_time).format(TimestampStyles.ShortTime))})\n\u200b"
            )
        embed.add_field(name=name, value=value, inline=False)

    async def fetch_schedule(self, user_id: str, guild_id: int | None = None) -> Embed:
        """Return an embed with the next 5 scheduled streams for ``user_id``."""
        try:
            schedule: ChannelStreamSchedule = await self.twitch.get_channel_stream_schedule(
                broadcaster_id=user_id,
                first=5,
            )
        except TwitchResourceNotFound as e:
            logger.error("No schedule found for user %s: %s", user_id, e)
            schedule = None
        except Exception as e:
            logger.error(f"Error fetching schedule for user {user_id}: {e}")
            schedule = None

        if guild_id is None:
            streamers = self.get_streamer_by_user_id(user_id)
            if streamers:
                guild_id = streamers[0].guild_id
            else:
                guild_id = int(self.enabled_servers[0]) if self.enabled_servers else 0

        try:
            bot = await self.bot.fetch_member(self.bot.user.id, guild_id)

            if schedule is not None:
                segments = schedule.segments
                embed = Embed(
                    title=(
                        f"<:TeamBelieve:808056449750138880> Planning de {schedule.broadcaster_name}"
                        " <:TeamBelieve:808056449750138880>"
                    ),
                    description="Les 5 prochains streams planifiés et dans moins de 10 jours.",
                    color=Colors.TWITCH,
                    timestamp=datetime.now(pytz.UTC),
                    footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
                )
                now = datetime.now(pytz.UTC)
                for stream in segments:
                    start_time = ensure_utc(stream.start_time)
                    end_time = ensure_utc(stream.end_time)
                    if start_time < now < end_time:
                        self.add_field_to_embed(embed, stream=stream, is_now=True)
                    elif start_time < now + timedelta(days=10):
                        self.add_field_to_embed(embed, stream=stream, is_now=False)
                return embed
            return Embed(
                title=(
                    "<:TeamBelieve:808056449750138880> Planning <:TeamBelieve:808056449750138880>"
                ),
                description="Aucun stream planifié",
                color=Colors.TWITCH,
                timestamp=datetime.now(pytz.UTC),
                footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
            )
        except Exception as e:
            logger.error(
                f"Error creating schedule embed for user {user_id} in guild {guild_id}: {e}"
            )
            return Embed(
                title="Planning",
                description="Erreur lors de la récupération du planning",
                color=Colors.ERROR,
                timestamp=datetime.now(pytz.UTC),
            )

    async def edit_message(
        self,
        streamer: StreamerInfo,
        offline: bool = False,
        data: ChannelUpdateEvent | StreamOfflineEvent | None = None,
    ) -> None:
        """Update the planning message (if set up) and sync the Discord scheduled event."""
        has_message = bool(streamer.message and streamer.channel)

        if offline is False:
            stream = await self.get_stream_data(streamer.user_id)

            if streamer.manage_discord_events or streamer.scheduled_event:
                try:
                    guild: Guild = await self.bot.fetch_guild(streamer.guild_id)
                    _, title, description, user_login = await self.get_stream_info(
                        stream, streamer.user_id, offline, data
                    )
                    title100 = title if len(title) <= 100 else f"{title[:97]}..."

                    if streamer.manage_discord_events:
                        if streamer.scheduled_event:
                            await streamer.scheduled_event.edit(
                                name=title100,
                                description=f"**{title}**\n\n{description}",
                                end_time=datetime.now(self.timezone) + timedelta(days=1),
                            )
                        else:
                            streamer.scheduled_event = await guild.create_scheduled_event(
                                name=title100,
                                event_type=ScheduledEventType.EXTERNAL,
                                external_location=f"https://twitch.tv/{user_login}",
                                start_time=datetime.now(self.timezone) + timedelta(seconds=5),
                                end_time=datetime.now(self.timezone) + timedelta(days=1),
                                description=f"**{title}**\n\n{description}",
                            )
                            await streamer.scheduled_event.edit(status=ScheduledEventStatus.ACTIVE)
                    elif streamer.scheduled_event:
                        await streamer.scheduled_event.delete()
                        streamer.scheduled_event = None
                except Exception as e:
                    logger.error(f"Error handling scheduled event for {streamer.streamer_id}: {e}")

            if has_message:
                try:
                    embed = await self.fetch_schedule(streamer.user_id, streamer.guild_id)
                    live_embed = await self.create_stream_embed(
                        stream, streamer.user_id, offline=False, data=data
                    )
                    await streamer.message.edit(
                        content="", embed=[embed, live_embed], components=[]
                    )
                except Exception as e:
                    logger.error(f"Error editing message for {streamer.streamer_id}: {e}")
        else:
            if streamer.scheduled_event:
                try:
                    await streamer.scheduled_event.delete()
                    streamer.scheduled_event = None
                except Exception as e:
                    logger.error(f"Error deleting scheduled event for {streamer.streamer_id}: {e}")

            if has_message:
                try:
                    embed = await self.fetch_schedule(streamer.user_id, streamer.guild_id)
                    offline_embed = await self.create_stream_embed(
                        None, streamer.user_id, offline=True, data=data
                    )
                    await streamer.message.edit(content="", embed=[embed, offline_embed])
                except Exception as e:
                    logger.error(f"Error editing message for {streamer.streamer_id}: {e}")

    @Task.create(
        OrTrigger(
            TimeTrigger(hour=2, utc=False),
            TimeTrigger(hour=6, utc=False),
            TimeTrigger(hour=10, utc=False),
            TimeTrigger(hour=14, utc=False),
            TimeTrigger(hour=18, utc=False),
            TimeTrigger(hour=22, utc=False),
        )
    )
    async def update(self):
        """Periodic healthcheck: restart EventSub if down, refresh every streamer."""
        if self.eventsub is None or self.eventsub.active_session is None:
            logger.warning("EventSub is not running")
            try:
                await self.eventsub.stop()
                await self.twitch.close()
            except Exception as e:
                logger.error("Error during cleanup: %s", e)
            await self.on_startup()

        for _, streamer in self.streamers.items():
            if streamer.user_id:
                try:
                    stream = await self.get_stream_data(streamer.user_id)
                    await self.edit_message(streamer, offline=(stream is None))
                except Exception as e:
                    logger.error(
                        f"Error updating streamer {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                    )
