import asyncio
import os
import signal
from datetime import datetime, timedelta
import json
from typing import Optional, Union

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


class TwitchExt2(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        config,module_config,enabled_servers = load_config("moduleTwitch")
        module_config = module_config[enabled_servers[0]]
        self.client_id = config["twitch"]["twitchClientId"]
        self.client_secret = config["twitch"]["twitchClientSecret"]
        self.coloc_guild_id = int(enabled_servers[0])
        self.planning_channel_id = int(module_config["twitchStreamerList"]["zerator"]["twitchPlanningChannelId"])
        self.planning_message_id = int(module_config["twitchStreamerList"]["zerator"]["twitchPlanningMessageId"])
        self.notification_channel_id = int(module_config["twitchStreamerList"]["zerator"]["twitchNotificationChannelId"])
        self.eventsub = None  # Initialize eventsub here
        self.twitch = None  # Initialize twitch here
        self.stop = False
        self.channel: BaseChannel = None
        self.message: Message = None
        self.notif_channel: BaseChannel = None
        self.user_id = None
        self.scheduled_event = None
        self.timezone = pytz.timezone("Europe/Paris")

    @listen()
    async def on_startup(self):
        """
        Perform actions when the bot starts up.
        """
        logger.info("Waiting for bot to be ready")
        await self.bot.wait_until_ready()
        self.channel: BaseChannel = await self.bot.fetch_channel(
            self.planning_channel_id
        )
        self.message: Message = await self.channel.fetch_message(
            self.planning_message_id
        )
        self.notif_channel: BaseChannel = await self.bot.fetch_channel(
            self.notification_channel_id
        )
        guild = await self.bot.fetch_guild(self.coloc_guild_id)
        for event in await guild.list_scheduled_events():
            creator = await event.creator
            if creator.id == self.bot.user.id:
                self.scheduled_event = event
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

    async def run(self):
        """
        Run the TwitchExt2 extension.
        """
        # create the api instance and get user auth either from storage or website
        self.twitch = await Twitch(self.client_id, self.client_secret)
        helper = UserAuthenticationStorageHelper(
            twitch=self.twitch,
            scopes=[AuthScope.USER_READ_SUBSCRIPTIONS],
            storage_path="./data/twitchcreds.json",
        )
        await helper.bind()
        # await self.twitch.set_user_authentication(
        #     self.access_token,
        #     [AuthScope.USER_READ_SUBSCRIPTIONS],
        #     self.refresh_token,
        # )

        user = await first(self.twitch.get_users(logins=["zerator"]))
        user_id = user.id
        self.user_id = user_id
        # create eventsub websocket instance and start the client.
        self.eventsub = EventSubWebsocket(
            self.twitch,
            callback_loop=asyncio.get_event_loop(),
            revocation_handler=self.on_revocation,
        )
        logger.info("Starting EventSub")
        self.eventsub.start()
        await self.eventsub.listen_stream_online(
            broadcaster_user_id=user_id, callback=self.on_live_start
        )
        await self.eventsub.listen_stream_offline(
            broadcaster_user_id=user_id, callback=self.on_live_end
        )
        await self.eventsub.listen_channel_update_v2(
            broadcaster_user_id=user_id, callback=self.on_update
        )
        await self.update()
        signal.signal(signal.SIGTERM, self.stop_on_signal)
        # Wait until the service is stopped
        while self.stop is False:
            await asyncio.sleep(1)

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

    async def fetch_schedule(self, user_id):
        """
        Fetches the schedule for a given user ID.

        Args:
            user_id (str): The ID of the user.
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

        if schedule is not None:
            bot = await self.bot.fetch_member(self.bot.user.id, self.coloc_guild_id)
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
            bot = await self.bot.fetch_member(self.bot.user.id, self.coloc_guild_id)
            embed = Embed(
                title="<:TeamBelieve:808056449750138880> Planning <:TeamBelieve:808056449750138880>",
                description="Aucun stream planifié",
                color=0x6441A5,
                timestamp=datetime.now(),
                footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
            )
            return embed

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

    async def create_embed(self, title, description, user_login):
        """
        Create an embed with the given title, description, and user login.

        Args:
            title (str): The title of the embed.
            description (str): The description of the embed.
            user_login (str): The user login.

        Returns:
            Embed: The created embed.
        """
        bot = await self.bot.fetch_member(self.bot.user.id, self.coloc_guild_id)

        return Embed(
            title=title,
            description=description,
            color=0x6441A5,
            url=f"https://twitch.tv/{user_login}",
            footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
            timestamp=datetime.now(),
        )

    async def edit_message(
        self,
        user_id: str,
        offline: bool = False,
        data: Optional[Union[ChannelUpdateEvent, StreamOfflineEvent]] = None,
    ) -> None:
        """
        Edit the message based on the user ID, offline status, and channel update event data.

        Args:
            user_id (str): The user ID.
            offline (bool, optional): The offline status. Defaults to False.
            data (ChannelUpdateEvent, optional): The channel update event data. Defaults to None.
        """
        embed = await self.fetch_schedule(user_id)
        if offline is False:
            stream = await self.get_stream_data(user_id)
            live_embed = await self.create_stream_embed(
                stream, user_id, offline=False, data=data
            )
            guild: Guild = await self.bot.fetch_guild(self.coloc_guild_id)
            user_id, title, description, user_login = await self.get_stream_info(
                stream, user_id, offline, data
            )
            title100 = title if len(title) <= 100 else f"{title[:97]}..."
            if self.scheduled_event:
                await self.scheduled_event.edit(
                    name=title100,
                    description=f"**{title}**\n\n{description}",
                    end_time=datetime.now(self.timezone) + timedelta(days=1),
                )
            else:
                self.scheduled_event = await guild.create_scheduled_event(
                    name=title100,
                    event_type=ScheduledEventType.EXTERNAL,
                    external_location=f"https://twitch.tv/{user_login}",
                    start_time=datetime.now(self.timezone) + timedelta(seconds=5),
                    end_time=datetime.now(self.timezone) + timedelta(days=1),
                    description=f"**{title}**\n\n{description}",
                )
                await self.scheduled_event.edit(status=ScheduledEventStatus.ACTIVE)
            await self.message.edit(
                content="", embed=[embed, live_embed], components=[]
            )
            self.update.reschedule(IntervalTrigger(minutes=15))
        else:
            offline_embed = await self.create_stream_embed(
                None, user_id, offline=True, data=data
            )
            if self.scheduled_event:
                await self.scheduled_event.delete()
                self.scheduled_event = None
            await self.message.edit(content="", embed=[embed, offline_embed])

    async def on_live_start(self, data: StreamOnlineEvent):
        """
        Handle the event when a live stream starts.

        Args:
            data (StreamOnlineEvent): The event data.
        """
        user_id = data.event.broadcaster_user_id
        logger.info("Stream is live: %s", data.event.broadcaster_user_name)

        await self.edit_message(user_id, offline=False)
        self.update.reschedule(IntervalTrigger(minutes=15))

    async def on_live_end(self, data: StreamOfflineEvent):
        """
        Handle the event when a live stream ends.

        Args:
            data (StreamOfflineEvent): The event data.
        """
        user_id = data.event.broadcaster_user_id
        logger.info("Stream is offline: %s", data.event.broadcaster_user_name)
        await self.edit_message(user_id, offline=True)
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

    async def get_stream_data(self, user_id):
        return await first(self.twitch.get_streams(user_id=user_id))

    async def on_update(self, data: ChannelUpdateEvent):
        user_name = data.event.broadcaster_user_name
        logger.info(
            "Channel updated: %s (ID : %s)\nCategory: %s(ID : %s)\nTitle: %s\nContent classification: %s\nLanguage: %s\n",
            user_name,
            data.event.broadcaster_user_id,
            data.event.category_name,
            data.event.category_id,
            data.event.title,
            ", ".join(data.event.content_classification_labels),
            data.event.language,
        )
        stream = await self.get_stream_data(data.event.broadcaster_user_id)
        if stream:
            await self.notif_channel.send(
                f"**{user_name}** a mis à jour le titre ou la catégorie du live.\nTitre : **{data.event.title}**\nCatégorie : **{data.event.category_name}**"
            )
            await self.edit_message(
                data.event.broadcaster_user_id, offline=False, data=data
            )
            self.update.reschedule(IntervalTrigger(minutes=15))
            return
        await self.edit_message(data.event.broadcaster_user_id, offline=True, data=data)
        await self.notif_channel.send(
            f" OMG live ??\n**{user_name}** a mis à jour le titre ou la catégorie du live.\nTitre : **{data.event.title}**\nCatégorie : **{data.event.category_name}**"
        )

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
        user: TwitchUser = await first(self.twitch.get_users(user_ids=[user_id]))
        status = "n'est pas en live" if offline else "est en live !"
        embed.set_author(
            name=f"{user.display_name} {status}",
            icon_url=user.profile_image_url,
            url=f"https://twitch.tv/{user.login}",
        )

        if offline:
            embed.set_image(
                url=f"{user.offline_image_url.format(width=1280, height=720)}?{datetime.now().timestamp()}"
            )

        return embed

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
        stream = await self.get_stream_data(self.user_id)
        if stream:
            await self.edit_message(self.user_id, offline=False)
        else:
            await self.edit_message(self.user_id, offline=True)

    # Task each hour to find if there are new twitch emotes using the twitch API
    @Task.create(OrTrigger(*[TimeTrigger(hour=i, utc=False) for i in range(24)]))
    # @Task.create(TimeTrigger(14, 52, 40, utc=False))
    async def check_new_emotes(self):
        logger.debug("Checking new emotes")
        emotes = await self.twitch.get_channel_emotes(self.user_id)
        # load the emotes from data/emotes.json
        with open("data/emotes.json", "r") as file:
            data = json.load(file)
        # check if there are new emotes
        new_emotes = [emote for emote in emotes if emote.id not in data]
        # Check if there are deleted emotes
        emote_ids = [emote.id for emote in emotes]
        deleted_emotes = [emote for emote in data if emote not in emote_ids]
        if new_emotes:
            logger.debug("New emotes found")
            bot = await self.bot.fetch_member(self.bot.user.id, self.coloc_guild_id)
            for emote in new_emotes:
                data[emote.id] = emote.name
                # Send a embed for each emote added
                # details
                if emote.emote_type == 'subscriptions':
                    if emote.tier == '1000':
                        tier = '1'
                    elif emote.tier == '2000':
                        tier = '2'
                    elif emote.tier == '3000':
                        tier = '3'
                    details = f"Sub tier {tier}"
                elif emote.emote_type == 'bitstier':
                    details = "Bits"
                elif emote.emote_type == 'follower':
                    details = "Follower"
                else:
                    details = "Other"
                    
                embed = Embed(
                    title="Nouvel emote ajouté",
                    description=f"L'emote {emote.name} a été ajouté à la chaine de ZeratoR ({details})",
                    color=0x6441A5,
                    timestamp=datetime.now(),
                    thumbnail=emote.images.get('url_4x', emote.images.get('url_2x', emote.images.get('url_1x'))),
                    footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url)
                )
                logger.info("New emote : %s", emote.name)
                await self.notif_channel.send(embed=embed)
        if deleted_emotes:
            logger.info("Deleted emotes found")
            bot = await self.bot.fetch_member(self.bot.user.id, self.coloc_guild_id)
            for emote in deleted_emotes:
                
                # Send a embed for each emote deleted
                embed = Embed(
                    title="Emote supprimé",
                    description=f"L'emote {data[emote]} a été supprimé de la chaine de ZeratoR :wave:",
                    color=0x6441A5,
                    timestamp=datetime.now(),
                    footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url)
                )
                logger.info("Deleted emote : %s", emote)
                await self.notif_channel.send(embed=embed)
                del data[emote]
        if new_emotes or deleted_emotes:
            # Save the new emotes
            with open("data/emotes.json", "w") as file:
                json.dump(data, file, indent=4)
