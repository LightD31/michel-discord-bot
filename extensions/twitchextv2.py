import asyncio
import os
import signal
from datetime import datetime, timedelta
import json
from typing import Optional, Union, Dict, List

import pytz
from interactions import (
    BaseChannel,
    Client,
    Embed,
    EmbedFooter,
    Extension,
    Guild,
    IntervalTrigger,
    Message,
    OrTrigger,
    ScheduledEventStatus,
    ScheduledEventType,
    Task,
    TimestampStyles,
    TimeTrigger,
    OrTrigger,
    listen,
    utils,
)
from twitchAPI.eventsub.websocket import EventSubWebsocket
from twitchAPI.helper import first
from twitchAPI.oauth import UserAuthenticationStorageHelper
from twitchAPI.object.api import (
    ChannelInformation,
    ChannelStreamSchedule,
    ChannelStreamScheduleSegment,
    Stream,
    TwitchUser,
)
from twitchAPI.object.eventsub import (
    ChannelUpdateEvent,
    StreamOfflineEvent,
    StreamOnlineEvent,
)
from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope, TwitchResourceNotFound

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))


class StreamerInfo:
    """Class to store information about a streamer"""

    def __init__(self, guild_id: int, streamer_id: str, config: dict):
        self.guild_id = guild_id
        self.streamer_id = streamer_id
        self.user_id = None
        self.planning_channel_id = int(config.get("twitchPlanningChannelId", 0))
        self.planning_message_id = int(config.get("twitchPlanningMessageId", 0))
        self.notification_channel_id = int(config.get("twitchNotificationChannelId", 0))
        self.channel = None
        self.message = None
        self.notif_channel = None
        self.scheduled_event = None


class TwitchExt2(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.config, self.module_config, self.enabled_servers = load_config("moduleTwitch")
        self.client_id = self.config["twitch"]["twitchClientId"]
        self.client_secret = self.config["twitch"]["twitchClientSecret"]

        # Initialize data structures for multiple servers and streamers
        self.streamers: Dict[str, StreamerInfo] = {}
        self.init_streamers()

        self.eventsub = None  # Initialize eventsub here
        self.twitch = None  # Initialize twitch here
        self.stop = False
        self.timezone = pytz.timezone("Europe/Paris")

    def init_streamers(self):
        """Initialize streamers for all enabled servers"""
        for guild_id in self.enabled_servers:
            server_config = self.module_config[guild_id]
            streamer_list = server_config.get("twitchStreamerList", {})

            for streamer_id, streamer_config in streamer_list.items():
                streamer_key = f"{guild_id}_{streamer_id}"
                self.streamers[streamer_key] = StreamerInfo(
                    guild_id=int(guild_id),
                    streamer_id=streamer_id,
                    config=streamer_config,
                )

    def get_streamer_by_user_id(self, user_id: str) -> List[StreamerInfo]:
        """Get all streamers that match the given Twitch user ID"""
        return [s for s in self.streamers.values() if s.user_id == user_id]

    def get_streamer_key(self, guild_id: int, streamer_id: str) -> str:
        """Get the unique key for a streamer"""
        return f"{guild_id}_{streamer_id}"

    @listen()
    async def on_startup(self):
        """
        Perform actions when the bot starts up.
        """
        logger.info("Waiting for bot to be ready")
        await self.bot.wait_until_ready()

        # Initialize channels and messages for all streamers
        for streamer_key, streamer in self.streamers.items():
            try:
                if streamer.planning_channel_id:
                    streamer.channel = await self.bot.fetch_channel(streamer.planning_channel_id)

                    if streamer.planning_message_id:
                        streamer.message = await streamer.channel.fetch_message(streamer.planning_message_id)

                if streamer.notification_channel_id:
                    streamer.notif_channel = await self.bot.fetch_channel(streamer.notification_channel_id)

                # Find any existing scheduled events created by the bot
                guild = await self.bot.fetch_guild(streamer.guild_id)
                for event in await guild.list_scheduled_events():
                    creator = await event.creator
                    if creator.id == self.bot.user.id:
                        # We should improve this to identify which streamer this event belongs to
                        # For now, associate it with the current streamer
                        streamer.scheduled_event = event
                        break
            except Exception as e:
                logger.error(f"Error initializing channels for streamer {streamer_key}: {e}")

        self.check_new_emotes.start()
        logger.info("Starting TwitchExt2")
        # asyncio.create_task(self.run())
        # self.update.start()

    @listen()
    async def on_ready(self):
        try:
            await self.eventsub.stop()
        except Exception as e:
            logger.info("EventSub is not running")
        await self.bot.wait_until_ready()
        asyncio.create_task(self.run())
        self.update.start()

    def stop_on_signal(self, signum, frame):
        """
        Stop the TwitchExt2 extension when a signal is received.
        """
        self.stop = True
        logger.info("Stopping TwitchExt2")
        asyncio.create_task(self.cleanup())

    async def cleanup(self):
        """
        Clean up resources and stop the TwitchExt2 extension.
        """
        try:
            await self.eventsub.stop()
            await self.twitch.close()
        except Exception as e:
            logger.error("Error during cleanup: %s", e)
        else:
            logger.info("TwitchExt2 stopped")
            await self.bot.stop()

    async def get_stream_data(self, user_id):
        """
        Get stream data for a specific user ID.

        Args:
            user_id (str): The Twitch user ID

        Returns:
            Stream or None: Stream object if the user is live, None otherwise
        """
        try:
            return await first(self.twitch.get_streams(user_id=user_id))
        except Exception as e:
            logger.error(f"Error getting stream data for user {user_id}: {e}")
            return None

    async def run(self):
        """
        Run the TwitchExt2 extension.
        """
        try:
            # create the api instance and get user auth either from storage or website
            self.twitch = await Twitch(self.client_id, self.client_secret)
            helper = UserAuthenticationStorageHelper(
                twitch=self.twitch,
                scopes=[AuthScope.USER_READ_SUBSCRIPTIONS],
                storage_path="./data/twitchcreds.json",
            )
            await helper.bind()

            # Initialize eventsub websocket instance
            self.eventsub = EventSubWebsocket(
                self.twitch,
                callback_loop=asyncio.get_event_loop(),
                revocation_handler=self.on_revocation,
            )
            logger.info("Starting EventSub")
            self.eventsub.start()

            # Subscribe to events for all streamers
            for streamer_key, streamer in self.streamers.items():
                try:
                    # Get the Twitch user ID for this streamer
                    user = await first(self.twitch.get_users(logins=[streamer.streamer_id]))
                    if user:
                        streamer.user_id = user.id

                        # Subscribe to events
                        await self.eventsub.listen_stream_online(
                            broadcaster_user_id=user.id, callback=self.on_live_start
                        )
                        await self.eventsub.listen_stream_offline(
                            broadcaster_user_id=user.id, callback=self.on_live_end
                        )
                        await self.eventsub.listen_channel_update_v2(
                            broadcaster_user_id=user.id, callback=self.on_update
                        )
                        logger.info(f"Registered event subscriptions for {streamer.streamer_id} (ID: {user.id})")
                    else:
                        logger.error(f"Could not find Twitch user for {streamer.streamer_id}")
                except Exception as e:
                    logger.error(f"Error subscribing to events for {streamer.streamer_id}: {e}")

            # Update all streamers initially
            await self.update()

            signal.signal(signal.SIGTERM, self.stop_on_signal)
            # Wait until the service is stopped
            while self.stop is False:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in run method: {e}")
            # Try to restart after a delay
            await asyncio.sleep(30)
            asyncio.create_task(self.run())

    @listen()
    async def on_revocation(self, data):
        logger.error("Revocation detected: %s", data)
        await self.eventsub.stop()
        await self.twitch.close()
        asyncio.create_task(self.run())

    def add_field_to_embed(
        self, embed: Embed, stream: ChannelStreamScheduleSegment, is_now=False
    ):
        now = datetime.now(pytz.UTC)
        title = stream.title if stream.title is not None else "Pas de titre défini"
        category = (
            stream.category.name
            if stream.category is not None
            else "Pas de catégorie définie"
        )
        if is_now:
            name = f"<:live_1:1265285043891343391><:live_2:1265285055186468864><:live_3:1265285063818477703> {title}"
            value = f"**{category}\nEn cours ({str(utils.timestamp_converter(stream.start_time).format(TimestampStyles.ShortTime))}-{str(utils.timestamp_converter(stream.end_time).format(TimestampStyles.ShortTime))})\n\u200b**"
        elif stream.start_time < now + timedelta(days=2):
            name = f"{title}"
            value = f"{category}\n{utils.timestamp_converter(stream.start_time).format(TimestampStyles.RelativeTime)} ({str(utils.timestamp_converter(stream.start_time).format(TimestampStyles.ShortTime))}-{str(utils.timestamp_converter(stream.end_time).format(TimestampStyles.ShortTime))})\n\u200b"
        else:
            name = f"{title}"
            value = f"{category}\n{utils.timestamp_converter(stream.start_time).format(TimestampStyles.LongDate)} ({str(utils.timestamp_converter(stream.start_time).format(TimestampStyles.ShortTime))}-{str(utils.timestamp_converter(stream.end_time).format(TimestampStyles.ShortTime))})\n\u200b"
        embed.add_field(name=name, value=value, inline=False)

    async def fetch_schedule(self, user_id, guild_id=None):
        """
        Fetches the schedule for a given user ID.

        Args:
            user_id (str): The ID of the user.
            guild_id (int, optional): The guild ID to fetch the bot member from.
        """
        try:
            schedule: ChannelStreamSchedule = (
                await self.twitch.get_channel_stream_schedule(
                    broadcaster_id=user_id,
                    first=5,
                )
            )
        except TwitchResourceNotFound as e:
            logger.error("No schedule found for user %s: %s", user_id, e)
            schedule = None
        except Exception as e:
            logger.error(f"Error fetching schedule for user {user_id}: {e}")
            schedule = None

        # Use the first streamer's guild ID if none is provided
        if guild_id is None:
            streamers = self.get_streamer_by_user_id(user_id)
            if streamers:
                guild_id = streamers[0].guild_id
            else:
                # Fallback to first enabled server if no streamer is found
                guild_id = int(self.enabled_servers[0]) if self.enabled_servers else 0

        try:
            bot = await self.bot.fetch_member(self.bot.user.id, guild_id)

            if schedule is not None:
                segments = schedule.segments
                embed = Embed(
                    title=f"<:TeamBelieve:808056449750138880> Planning de {schedule.broadcaster_name} <:TeamBelieve:808056449750138880>",
                    description="Les 5 prochains streams planifiés et dans moins de 10 jours.",
                    color=0x6441A5,
                    timestamp=datetime.now(pytz.UTC),
                    footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
                )
                now = datetime.now(pytz.UTC)
                for stream in segments:
                    if stream.start_time < now < stream.end_time:
                        self.add_field_to_embed(embed, stream=stream, is_now=True)
                    elif stream.start_time < now + timedelta(days=10):
                        self.add_field_to_embed(embed, stream=stream, is_now=False)
                return embed
            else:
                embed = Embed(
                    title="<:TeamBelieve:808056449750138880> Planning <:TeamBelieve:808056449750138880>",
                    description="Aucun stream planifié",
                    color=0x6441A5,
                    timestamp=datetime.now(),
                    footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
                )
                return embed
        except Exception as e:
            logger.error(f"Error creating schedule embed for user {user_id} in guild {guild_id}: {e}")
            # Return a basic embed if there's an error
            return Embed(
                title="Planning",
                description="Erreur lors de la récupération du planning",
                color=0xFF0000,
                timestamp=datetime.now(),
            )

    async def create_stream_embed(
        self,
        stream: Optional[Stream],
        user_id: str,
        offline: bool = False,
        data: Optional[ChannelUpdateEvent] = None,
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
        stream: Optional[Stream],
        user_id: str,
        offline: bool,
        data: Optional[ChannelUpdateEvent] = None,
    ) -> tuple:
        if data:
            return self.get_stream_info_from_data(data, offline, stream)
        elif offline:
            return await self.get_offline_stream_info(user_id)
        else:
            return self.get_online_stream_info(stream)

    def get_stream_info_from_data(
        self, data: ChannelUpdateEvent, offline, stream: Stream = None
    ):
        """
        Get the stream information from the data.

        Args:
            data (ChannelUpdateEvent): The channel update event data.
            offline (bool): Flag indicating if the stream is offline.
            stream (Stream, optional): The stream object. Defaults to None.

        Returns:
            tuple: A tuple containing the user ID, title, description, and user login.
        """
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

    async def get_offline_stream_info(self, user_id):
        """
        Get the offline stream information.

        Args:
            user_id (str): The ID of the user.

        Returns:
            tuple: A tuple containing the user ID, title, description, and user login.
        """
        channel_infos = await self.twitch.get_channel_information(user_id)
        channel: ChannelInformation = channel_infos[0] if channel_infos else None
        game = channel.game_name
        description = f"Ne joue pas à **{game}**"
        title = channel.title
        user_login = channel.broadcaster_login
        return user_id, title, description, user_login

    def get_online_stream_info(self, stream: Stream):
        """
        Get the online stream information.

        Args:
            stream (Stream): The stream object.

        Returns:
            tuple: A tuple containing the user ID, title, description, and user login.
        """
        description = (
            f"Joue à **{stream.game_name}** pour **{stream.viewer_count}** viewers"
        )
        title = stream.title
        user_login = stream.user_login
        return stream.user_id, title, description, user_login

    async def create_embed(self, title, description, user_login, guild_id=None):
        """
        Create an embed with the given title, description, and user login.

        Args:
            title (str): The title of the embed.
            description (str): The description of the embed.
            user_login (str): The user login.
            guild_id (int, optional): The guild ID to fetch the bot member from.

        Returns:
            Embed: The created embed.
        """
        # Use the first streamer's guild if none is provided
        if guild_id is None:
            if self.enabled_servers:
                guild_id = int(self.enabled_servers[0])
            else:
                # Create a basic embed without footer if no guild is available
                return Embed(
                    title=title,
                    description=description,
                    color=0x6441A5,
                    url=f"https://twitch.tv/{user_login}",
                    timestamp=datetime.now(),
                )

        try:
            bot = await self.bot.fetch_member(self.bot.user.id, guild_id)
            return Embed(
                title=title,
                description=description,
                color=0x6441A5,
                url=f"https://twitch.tv/{user_login}",
                footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Error creating embed for {user_login} in guild {guild_id}: {e}")
            # Return a basic embed without footer if there's an error
            return Embed(
                title=title,
                description=description,
                color=0x6441A5,
                url=f"https://twitch.tv/{user_login}",
                timestamp=datetime.now(),
            )

    async def edit_message(
        self,
        streamer: StreamerInfo,
        offline: bool = False,
        data: Optional[Union[ChannelUpdateEvent, StreamOfflineEvent]] = None,
    ) -> None:
        """
        Edit the message for a specific streamer.

        Args:
            streamer (StreamerInfo): The streamer info object.
            offline (bool, optional): The offline status. Defaults to False.
            data (Union[ChannelUpdateEvent, StreamOfflineEvent], optional): Event data. Defaults to None.
        """
        if not streamer.message or not streamer.channel:
            logger.warning(f"Missing message or channel for {streamer.streamer_id} in guild {streamer.guild_id}")
            return

        embed = await self.fetch_schedule(streamer.user_id, streamer.guild_id)

        if offline is False:
            stream = await self.get_stream_data(streamer.user_id)
            live_embed = await self.create_stream_embed(
                stream, streamer.user_id, offline=False, data=data
            )
            guild: Guild = await self.bot.fetch_guild(streamer.guild_id)
            user_id, title, description, user_login = await self.get_stream_info(
                stream, streamer.user_id, offline, data
            )

            title100 = title if len(title) <= 100 else f"{title[:97]}..."

            # Handle scheduled event
            try:
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
            except Exception as e:
                logger.error(f"Error handling scheduled event for {streamer.streamer_id}: {e}")

            try:
                await streamer.message.edit(
                    content="", embed=[embed, live_embed], components=[]
                )
            except Exception as e:
                logger.error(f"Error editing message for {streamer.streamer_id}: {e}")
        else:
            offline_embed = await self.create_stream_embed(
                None, streamer.user_id, offline=True, data=data
            )

            try:
                if streamer.scheduled_event:
                    await streamer.scheduled_event.delete()
                    streamer.scheduled_event = None
            except Exception as e:
                logger.error(f"Error deleting scheduled event for {streamer.streamer_id}: {e}")

            try:
                await streamer.message.edit(content="", embed=[embed, offline_embed])
            except Exception as e:
                logger.error(f"Error editing message for {streamer.streamer_id}: {e}")

    async def add_user(
        self, embed: Embed, user_id: str, offline: bool = False
    ) -> Embed:
        """
        Add a user to the embed with the specified user ID.

        Args:
            embed (Embed): The embed to add the user to.
            user_id (str): The ID of the user.
            offline (bool, optional): Whether the user is offline. Defaults to False.

        Returns:
            Embed: The updated embed.
        """
        try:
            user: TwitchUser = await first(self.twitch.get_users(user_ids=[user_id]))
            status = "n'est pas en live" if offline else "est en live !"
            embed.set_author(
                name=f"{user.display_name} {status}",
                icon_url=user.profile_image_url,
                url=f"https://twitch.tv/{user.login}",
            )

            if offline and hasattr(user, 'offline_image_url') and user.offline_image_url:
                embed.set_image(
                    url=f"{user.offline_image_url}?{datetime.now().timestamp()}"
                )
            
            return embed
        except Exception as e:
            logger.error(f"Error adding user {user_id} to embed: {e}")
            return embed

    async def on_live_start(self, data: StreamOnlineEvent):
        """
        Handle the event when a live stream starts.

        Args:
            data (StreamOnlineEvent): The event data.
        """
        user_id = data.event.broadcaster_user_id
        logger.info("Stream is live: %s", data.event.broadcaster_user_name)

        # Find all streamers matching this user_id
        streamers = self.get_streamer_by_user_id(user_id)
        for streamer in streamers:
            try:
                # For each server tracking this streamer, update the message
                await self.edit_message(streamer, offline=False)
            except Exception as e:
                logger.error(f"Error handling live start for {streamer.streamer_id} in guild {streamer.guild_id}: {e}")

        self.update.reschedule(IntervalTrigger(minutes=15))

    async def on_live_end(self, data: StreamOfflineEvent):
        """
        Handle the event when a live stream ends.

        Args:
            data (StreamOfflineEvent): The event data.
        """
        user_id = data.event.broadcaster_user_id
        logger.info("Stream is offline: %s", data.event.broadcaster_user_name)

        # Find all streamers matching this user_id
        streamers = self.get_streamer_by_user_id(user_id)
        for streamer in streamers:
            try:
                # For each server tracking this streamer, update the message
                await self.edit_message(streamer, offline=True)
            except Exception as e:
                logger.error(f"Error handling live end for {streamer.streamer_id} in guild {streamer.guild_id}: {e}")

        self.update.reschedule(
            OrTrigger(
                TimeTrigger(hour=2, utc=False),
                TimeTrigger(hour=6, utc=False),
                TimeTrigger(hour=10, utc=False),
                TimeTrigger(hour=14, utc=False),
                TimeTrigger(hour=18, utc=False),
                TimeTrigger(hour=22, utc=False),
            )
        )

    async def on_update(self, data: ChannelUpdateEvent):
        user_id = data.event.broadcaster_user_id
        user_name = data.event.broadcaster_user_name
        logger.info(
            "Channel updated: %s (ID : %s)\nCategory: %s(ID : %s)\nTitle: %s\nContent classification: %s\nLanguage: %s\n",
            user_name,
            user_id,
            data.event.category_name,
            data.event.category_id,
            data.event.title,
            ", ".join(data.event.content_classification_labels),
            data.event.language,
        )

        # Find all streamers matching this user_id
        streamers = self.get_streamer_by_user_id(user_id)
        for streamer in streamers:
            try:
                stream = await self.get_stream_data(user_id)

                # Send notification
                if streamer.notif_channel:
                    update_msg = f"**{user_name}** a mis à jour le titre ou la catégorie du live.\nTitre : **{data.event.title}**\nCatégorie : **{data.event.category_name}**"

                    if not stream:
                        update_msg = f" OMG live ??\n{update_msg}"

                    await streamer.notif_channel.send(update_msg)

                # Update the message
                await self.edit_message(
                    streamer,
                    offline=(stream is None),
                    data=data
                )
            except Exception as e:
                logger.error(f"Error handling update for {streamer.streamer_id} in guild {streamer.guild_id}: {e}")

        if any(await self.get_stream_data(s.user_id) for s in self.streamers.values()):
            self.update.reschedule(IntervalTrigger(minutes=15))

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
        # Check EventSub Status
        if self.eventsub is None or self.eventsub.active_session is None:
            logger.warning("EventSub is not running")
            try:
                await self.eventsub.stop()
                await self.twitch.close()
            except Exception as e:
                logger.error("Error during cleanup: %s", e)
            await self.on_startup()

        # Update all streamers
        for streamer_key, streamer in self.streamers.items():
            if streamer.user_id:
                try:
                    stream = await self.get_stream_data(streamer.user_id)
                    await self.edit_message(streamer, offline=(stream is None))
                except Exception as e:
                    logger.error(f"Error updating streamer {streamer.streamer_id} in guild {streamer.guild_id}: {e}")

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, utc=False) for i in range(24)]))
    async def check_new_emotes(self):
        logger.debug("Checking new emotes")
        # Check emotes for all streamers
        for streamer_key, streamer in self.streamers.items():
            if not streamer.user_id or not streamer.notif_channel:
                continue

            try:
                emotes = await self.twitch.get_channel_emotes(streamer.user_id)
                emote_file = f"data/emotes_{streamer.guild_id}_{streamer.streamer_id}.json"

                # Load existing emotes or create empty dict
                data = {}
                try:
                    if os.path.exists(emote_file):
                        with open(emote_file, "r") as file:
                            data = json.load(file)
                except Exception as e:
                    logger.error(f"Error loading emotes file for {streamer.streamer_id}: {e}")

                # Check for new and deleted emotes
                new_emotes = [emote for emote in emotes if emote.id not in data]
                emote_ids = [emote.id for emote in emotes]
                deleted_emotes = [emote for emote in data if emote not in emote_ids]

                # Process new emotes
                if new_emotes:
                    logger.debug(f"New emotes found for {streamer.streamer_id}")
                    bot = await self.bot.fetch_member(self.bot.user.id, streamer.guild_id)
                    for emote in new_emotes:
                        data[emote.id] = emote.name

                        # Determine emote details
                        if emote.emote_type == 'subscriptions':
                            tier = '1' if emote.tier == '1000' else '2' if emote.tier == '2000' else '3'
                            details = f"Sub tier {tier}"
                        elif emote.emote_type == 'bitstier':
                            details = "Bits"
                        elif emote.emote_type == 'follower':
                            details = "Follower"
                        else:
                            details = "Other"

                        embed = Embed(
                            title="Nouvel emote ajouté",
                            description=f"L'emote {emote.name} a été ajouté à la chaine de {streamer.streamer_id} ({details})",
                            color=0x6441A5,
                            timestamp=datetime.now(),
                            thumbnail=emote.images.get('url_4x', emote.images.get('url_2x', emote.images.get('url_1x'))),
                            footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url)
                        )
                        logger.info(f"New emote for {streamer.streamer_id}: {emote.name}")
                        await streamer.notif_channel.send(embed=embed)

                # Process deleted emotes
                if deleted_emotes:
                    logger.info(f"Deleted emotes found for {streamer.streamer_id}")
                    bot = await self.bot.fetch_member(self.bot.user.id, streamer.guild_id)
                    for emote in deleted_emotes:
                        embed = Embed(
                            title="Emote supprimé",
                            description=f"L'emote {data[emote]} a été supprimé de la chaine de {streamer.streamer_id} :wave:",
                            color=0x6441A5,
                            timestamp=datetime.now(),
                            footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url)
                        )
                        logger.info(f"Deleted emote for {streamer.streamer_id}: {emote}")
                        await streamer.notif_channel.send(embed=embed)
                        del data[emote]

                # Save updated emotes
                if new_emotes or deleted_emotes:
                    with open(emote_file, "w") as file:
                        json.dump(data, file, indent=4)

            except Exception as e:
                logger.error(f"Error checking emotes for {streamer.streamer_id}: {e}")
