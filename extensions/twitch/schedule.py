"""Twitch schedule embed + planning message + Discord scheduled-event sync."""

import contextlib
import os
from datetime import datetime, timedelta

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
from interactions.client.errors import NotFound
from twitchAPI.object.api import ChannelStreamSchedule, ChannelStreamScheduleSegment
from twitchAPI.object.eventsub import ChannelUpdateEvent, StreamOfflineEvent
from twitchAPI.type import TwitchResourceNotFound

from src.core import clock
from src.core import logging as logutil
from src.core.logging import log_failure
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import (
    edit_message_if_changed,
    fetch_or_create_persistent_message,
    forget_persistent_message,
)

from ._common import StreamerInfo, ensure_utc

logger = logutil.init_logger(__name__)


class ScheduleMixin:
    """Fetches Twitch schedules, updates the planning message, and manages the
    per-streamer Discord scheduled event.
    """

    async def _planning_message(self, streamer: StreamerInfo):
        """The streamer's planning message, fetched or posted; ``None`` when not set up."""
        if not streamer.planning_channel_id:
            return None
        return await fetch_or_create_persistent_message(
            self.bot,
            guild_id=streamer.guild_id,
            slot=streamer.planning_slot,
            channel_id=streamer.planning_channel_id,
            label=f"Planning Twitch {streamer.streamer_id}",
            initial_content=f"Initialisation du planning de {streamer.streamer_id}…",
            pin=streamer.planning_pin,
            logger=logger,
        )

    def add_field_to_embed(
        self,
        embed: Embed,
        stream: ChannelStreamScheduleSegment,
        is_now: bool = False,
    ) -> None:
        now = clock.now()
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
            log_failure(logger, f"Error fetching schedule for user {user_id}: {e}")
            schedule = None

        if guild_id is None:
            streamers = self.get_streamer_by_user_id(user_id)
            guild_id = streamers[0].guild_id if streamers else 0

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
                    timestamp=clock.now(),
                    footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
                )
                now = clock.now()
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
                timestamp=clock.now(),
                footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
            )
        except Exception as e:
            log_failure(
                logger, f"Error creating schedule embed for user {user_id} in guild {guild_id}: {e}"
            )
            return Embed(
                title="Planning",
                description="Erreur lors de la récupération du planning",
                color=Colors.ERROR,
                timestamp=clock.now(),
            )

    async def edit_message(
        self,
        streamer: StreamerInfo,
        offline: bool = False,
        data: ChannelUpdateEvent | StreamOfflineEvent | None = None,
    ) -> None:
        """Update the planning message (if set up) and sync the Discord scheduled event."""
        message = await self._planning_message(streamer)

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
                            try:
                                await streamer.scheduled_event.edit(
                                    name=title100,
                                    description=f"**{title}**\n\n{description}",
                                    end_time=datetime.now(self.timezone) + timedelta(days=1),
                                )
                            except NotFound:
                                # Event was deleted in Discord; drop the stale reference
                                # so we recreate it below.
                                logger.warning(
                                    f"Scheduled event for {streamer.streamer_id} no longer exists, recreating"
                                )
                                streamer.scheduled_event = None
                        if not streamer.scheduled_event:
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
                        with contextlib.suppress(NotFound):
                            await streamer.scheduled_event.delete()
                        streamer.scheduled_event = None
                except Exception as e:
                    log_failure(
                        logger, f"Error handling scheduled event for {streamer.streamer_id}: {e}"
                    )

            if message:
                try:
                    embed = await self.fetch_schedule(streamer.user_id, streamer.guild_id)
                    live_embed = await self.create_stream_embed(
                        stream,
                        streamer.user_id,
                        offline=False,
                        data=data,
                        guild_id=streamer.guild_id,
                    )
                    await edit_message_if_changed(
                        message,
                        content="",
                        embed=[embed, live_embed],
                        components=[],
                        ignore_timestamp=True,
                        logger=logger,
                    )
                except Exception as e:
                    log_failure(logger, f"Error editing message for {streamer.streamer_id}: {e}")
                    # Deleted from Discord, say: resolved again (or reposted) next time.
                    forget_persistent_message(streamer.guild_id, streamer.planning_slot)
        else:
            if streamer.scheduled_event:
                try:
                    await streamer.scheduled_event.delete()
                except NotFound:
                    pass
                except Exception as e:
                    log_failure(
                        logger, f"Error deleting scheduled event for {streamer.streamer_id}: {e}"
                    )
                streamer.scheduled_event = None

            if message:
                try:
                    embed = await self.fetch_schedule(streamer.user_id, streamer.guild_id)
                    offline_embed = await self.create_stream_embed(
                        None, streamer.user_id, offline=True, data=data, guild_id=streamer.guild_id
                    )
                    await edit_message_if_changed(
                        message,
                        content="",
                        embed=[embed, offline_embed],
                        ignore_timestamp=True,
                        logger=logger,
                    )
                except Exception as e:
                    log_failure(logger, f"Error editing message for {streamer.streamer_id}: {e}")
                    forget_persistent_message(streamer.guild_id, streamer.planning_slot)

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
        """Periodic refresh of every streamer's planning message and Discord event.

        EventSub liveness is handled by ``eventsub_watchdog``; this used to try
        to recover it by calling ``on_startup()``, which only re-fetches
        channels and never restarts the websocket.
        """
        if self.twitch is None:
            logger.warning("Twitch client is not ready, skipping update")
            return

        for _, streamer in self.streamers.items():
            if streamer.user_id:
                try:
                    stream = await self.get_stream_data(streamer.user_id)
                    await self.edit_message(streamer, offline=(stream is None))
                except Exception as e:
                    log_failure(
                        logger,
                        f"Error updating streamer {streamer.streamer_id} in guild {streamer.guild_id}: {e}",
                    )
