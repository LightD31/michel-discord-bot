import asyncio
import json
import os
import signal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

import aiohttp
import pytz
from interactions import (
    AutocompleteContext,
    BaseChannel,
    ChannelType,
    Client,
    Embed,
    EmbedFooter,
    Extension,
    File,
    Guild,
    IntervalTrigger,
    Message,
    OptionType,
    OrTrigger,
    Permissions,
    ScheduledEventStatus,
    ScheduledEventType,
    SlashContext,
    Task,
    TimestampStyles,
    TimeTrigger,
    listen,
    slash_command,
    slash_default_member_permission,
    slash_option,
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
from src.config_manager import CONFIG_PATH, load_config
from src.helpers import Colors, send_error, send_success
from src.mongodb import mongo_manager


def _save_streamer_channel_message(
    guild_id: str, streamer_id: str, channel_id: str, message_id: str, pin: bool
) -> None:
    """Persist planning channel/message for a specific streamer in config.json."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    guild = data.setdefault("servers", {}).setdefault(str(guild_id), {})
    mod = guild.setdefault("moduleTwitch", {})
    streamer_list = mod.setdefault("twitchStreamerList", {})
    streamer_cfg = streamer_list.setdefault(streamer_id, {})
    streamer_cfg["twitchPlanningChannelId"] = str(channel_id)
    streamer_cfg["twitchPlanningMessageId"] = str(message_id)
    streamer_cfg["twitchPlanningPinMessage"] = pin
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


logger = logutil.init_logger(os.path.basename(__file__))


def ensure_utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware in UTC"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume naive datetime is in UTC
        return pytz.UTC.localize(dt)
    return dt.astimezone(pytz.UTC)


class StreamerInfo:
    """Class to store information about a streamer"""

    def __init__(self, guild_id: int, streamer_id: str, config: dict):
        self.guild_id = guild_id
        self.streamer_id = streamer_id
        self.user_id = None
        self.planning_channel_id = int(config.get("twitchPlanningChannelId", 0))
        self.planning_message_id = int(config.get("twitchPlanningMessageId", 0))
        self.planning_pin = bool(config.get("twitchPlanningPinMessage", False))
        self.notification_channel_id = int(config.get("twitchNotificationChannelId", 0))
        # Per-streamer notification settings
        self.notify_stream_start = bool(config.get("notifyStreamStart", False))
        self.notify_stream_update = bool(config.get("notifyStreamUpdate", False))
        self.notify_stream_end = bool(config.get("notifyStreamEnd", False))
        self.notify_emote_changes = bool(config.get("notifyEmoteChanges", False))
        self.manage_discord_events = bool(config.get("manageDiscordEvents", False))
        self.channel = None
        self.message = None
        self.notif_channel = None
        self.scheduled_event = None
        # Stream session info (stored when stream starts)
        self.stream_start_time: datetime | None = None
        self.stream_title: str | None = None
        self.stream_id: str | None = None
        # Last notified title and category (to avoid duplicate notifications)
        self.last_notified_title: str | None = None
        self.last_notified_category: str | None = None
        # Ordered list of categories played during the current live session
        self.stream_categories: list[str] = []


class TwitchExtension(Extension):
    # Directory for caching emote images
    EMOTE_CACHE_DIR = "data/emote_cache"
    DEFAULT_EMBED_COLOR = Colors.TWITCH

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.config, self.module_config, self.enabled_servers = load_config("moduleTwitch")
        self.client_id = self.config["twitch"]["twitchClientId"]
        self.client_secret = self.config["twitch"]["twitchClientSecret"]

        # Initialize data structures for multiple servers and streamers
        self.streamers: dict[str, StreamerInfo] = {}
        self.init_streamers()

        self.eventsub = None  # Initialize eventsub here
        self.twitch = None  # Initialize twitch here
        self.stop = False
        self.timezone = pytz.timezone("Europe/Paris")

        # Ensure emote cache directory exists
        os.makedirs(self.EMOTE_CACHE_DIR, exist_ok=True)

    @staticmethod
    def get_display_value(value: str | None, fallback: str = "Non renseigné") -> str:
        """Normalize optional values before displaying them in notifications."""
        if value is None:
            return fallback
        normalized = str(value).strip()
        return normalized if normalized else fallback

    @staticmethod
    def get_emote_details(emote) -> str:
        """Return a normalized text label for the emote source/type."""
        if emote.emote_type == "subscriptions":
            tier = "1" if emote.tier == "1000" else "2" if emote.tier == "2000" else "3"
            return f"Sub tier {tier}"
        if emote.emote_type == "bitstier":
            return "Bits"
        if emote.emote_type == "follower":
            return "Follower"
        return "Autre"

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

    async def download_emote_image(
        self, emote_id: str, image_url: str, streamer_id: str
    ) -> str | None:
        """
        Download and cache an emote image locally.

        Args:
            emote_id: The Twitch emote ID
            image_url: The URL of the emote image
            streamer_id: The streamer ID

        Returns:
            The local file path if successful, None otherwise
        """
        if not image_url:
            return None

        file_path = os.path.join(self.EMOTE_CACHE_DIR, f"{streamer_id}_{emote_id}.png")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        content = await response.read()
                        with open(file_path, "wb") as f:
                            f.write(content)
                        logger.debug(f"Cached emote image: {file_path}")
                        return file_path
                    else:
                        logger.error(
                            f"Failed to download emote image {emote_id}: HTTP {response.status}"
                        )
                        return None
        except Exception as e:
            logger.error(f"Error downloading emote image {emote_id}: {e}")
            return None

    def get_cached_emote_path(self, emote_id: str, streamer_id: str) -> str | None:
        """
        Get the path to a cached emote image if it exists.

        Args:
            emote_id: The Twitch emote ID
            streamer_id: The streamer ID

        Returns:
            The local file path if exists, None otherwise
        """
        file_path = os.path.join(self.EMOTE_CACHE_DIR, f"{streamer_id}_{emote_id}.png")
        return file_path if os.path.exists(file_path) else None

    def delete_cached_emote(self, emote_id: str, streamer_id: str) -> None:
        """
        Delete a cached emote image.

        Args:
            emote_id: The Twitch emote ID
            streamer_id: The streamer ID
        """
        file_path = os.path.join(self.EMOTE_CACHE_DIR, f"{streamer_id}_{emote_id}.png")
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug(f"Deleted cached emote: {file_path}")
        except Exception as e:
            logger.error(f"Error deleting cached emote {emote_id}: {e}")

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

    def get_streamer_by_user_id(self, user_id: str) -> list[StreamerInfo]:
        """Get all streamers that match the given Twitch user ID"""
        return [s for s in self.streamers.values() if s.user_id == user_id]

    @staticmethod
    def _streamer_state_collection():
        return mongo_manager.get_global_collection("twitch_streamer_state")

    async def _load_streamer_states(self) -> None:
        """Hydrate StreamerInfo session fields from MongoDB at startup."""
        try:
            states: dict[str, dict] = {}
            async for doc in self._streamer_state_collection().find():
                states[doc["_id"]] = doc
        except Exception as e:
            logger.error("Failed to load Twitch streamer states: %s", e)
            return

        for streamer in self.streamers.values():
            doc = states.get(streamer.streamer_id)
            if not doc:
                continue
            start = doc.get("stream_start_time")
            streamer.stream_start_time = ensure_utc(start) if start else None
            streamer.stream_title = doc.get("stream_title")
            streamer.stream_id = doc.get("stream_id")
            streamer.last_notified_title = doc.get("last_notified_title")
            streamer.last_notified_category = doc.get("last_notified_category")
            stored_categories = doc.get("stream_categories")
            streamer.stream_categories = (
                list(stored_categories) if isinstance(stored_categories, list) else []
            )

    async def _save_streamer_state(self, streamer_id: str) -> None:
        """Persist the session fields of a streamer (shared across guilds)."""
        ref = next(
            (s for s in self.streamers.values() if s.streamer_id == streamer_id),
            None,
        )
        if ref is None:
            return
        doc = {
            "stream_start_time": ref.stream_start_time,
            "stream_title": ref.stream_title,
            "stream_id": ref.stream_id,
            "last_notified_title": ref.last_notified_title,
            "last_notified_category": ref.last_notified_category,
            "stream_categories": list(ref.stream_categories),
        }
        try:
            await self._streamer_state_collection().update_one(
                {"_id": streamer_id}, {"$set": doc}, upsert=True
            )
        except Exception as e:
            logger.error("Failed to save Twitch state for %s: %s", streamer_id, e)

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

        # Restore persisted stream session state before reacting to any events
        await self._load_streamer_states()

        # Initialize channels and messages for all streamers
        for streamer_key, streamer in self.streamers.items():
            try:
                if streamer.planning_channel_id:
                    streamer.channel = await self.bot.fetch_channel(streamer.planning_channel_id)

                    msg = None
                    if (
                        streamer.planning_message_id
                        and streamer.channel
                        and hasattr(streamer.channel, "fetch_message")
                    ):
                        try:
                            msg = await streamer.channel.fetch_message(streamer.planning_message_id)
                        except Exception as e:
                            logger.warning(
                                "Streamer %s: could not fetch planning message %s (%s); recreating",
                                streamer.streamer_id,
                                streamer.planning_message_id,
                                e,
                            )
                    if msg is None and streamer.channel and hasattr(streamer.channel, "send"):
                        msg = await streamer.channel.send(
                            f"Initialisation du planning de {streamer.streamer_id}…"
                        )
                        if streamer.planning_pin:
                            try:
                                await msg.pin()
                            except Exception as e:
                                logger.warning("Could not pin planning message: %s", e)
                        _save_streamer_channel_message(
                            str(streamer.guild_id),
                            streamer.streamer_id,
                            str(streamer.channel.id),
                            str(msg.id),
                            streamer.planning_pin,
                        )
                        streamer.planning_message_id = int(msg.id)
                    streamer.message = msg

                if streamer.notification_channel_id:
                    streamer.notif_channel = await self.bot.fetch_channel(
                        streamer.notification_channel_id
                    )

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
        logger.info("Starting TwitchExtension")
        # asyncio.create_task(self.run())
        # self.update.start()

    @listen()
    async def on_ready(self):
        try:
            await self.eventsub.stop()
        except Exception:
            logger.info("EventSub is not running")
        await self.bot.wait_until_ready()
        asyncio.create_task(self.run())
        self.update.start()

    def stop_on_signal(self, signum, frame):
        """
        Stop the TwitchExtension extension when a signal is received.
        """
        self.stop = True
        logger.info("Stopping TwitchExtension")
        asyncio.create_task(self.cleanup())

    async def cleanup(self):
        """
        Clean up resources and stop the TwitchExtension extension.
        """
        try:
            await self.eventsub.stop()
            await self.twitch.close()
        except Exception as e:
            logger.error("Error during cleanup: %s", e)
        else:
            logger.info("TwitchExtension stopped")
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
        Run the TwitchExtension extension.
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
                        logger.info(
                            f"Registered event subscriptions for {streamer.streamer_id} (ID: {user.id})"
                        )
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

    def add_field_to_embed(self, embed: Embed, stream: ChannelStreamScheduleSegment, is_now=False):
        now = datetime.now(pytz.UTC)
        start_time = ensure_utc(stream.start_time)
        end_time = ensure_utc(stream.end_time)
        title = stream.title if stream.title is not None else "Pas de titre défini"
        category = (
            stream.category.name if stream.category is not None else "Pas de catégorie définie"
        )
        if is_now:
            name = f"<:live_1:1265285043891343391><:live_2:1265285055186468864><:live_3:1265285063818477703> {title}"
            value = f"**{category}\nEn cours ({str(utils.timestamp_converter(start_time).format(TimestampStyles.ShortTime))}-{str(utils.timestamp_converter(end_time).format(TimestampStyles.ShortTime))})\n\u200b**"
        elif start_time < now + timedelta(days=2):
            name = f"{title}"
            value = f"{category}\n{utils.timestamp_converter(start_time).format(TimestampStyles.RelativeTime)} ({str(utils.timestamp_converter(start_time).format(TimestampStyles.ShortTime))}-{str(utils.timestamp_converter(end_time).format(TimestampStyles.ShortTime))})\n\u200b"
        else:
            name = f"{title}"
            value = f"{category}\n{utils.timestamp_converter(start_time).format(TimestampStyles.LongDate)} ({str(utils.timestamp_converter(start_time).format(TimestampStyles.ShortTime))}-{str(utils.timestamp_converter(end_time).format(TimestampStyles.ShortTime))})\n\u200b"
        embed.add_field(name=name, value=value, inline=False)

    async def fetch_schedule(self, user_id, guild_id=None):
        """
        Fetches the schedule for a given user ID.

        Args:
            user_id (str): The ID of the user.
            guild_id (int, optional): The guild ID to fetch the bot member from.
        """
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
            else:
                embed = Embed(
                    title="<:TeamBelieve:808056449750138880> Planning <:TeamBelieve:808056449750138880>",
                    description="Aucun stream planifié",
                    color=Colors.TWITCH,
                    timestamp=datetime.now(pytz.UTC),
                    footer=EmbedFooter(text=bot.display_name, icon_url=bot.avatar_url),
                )
                return embed
        except Exception as e:
            logger.error(
                f"Error creating schedule embed for user {user_id} in guild {guild_id}: {e}"
            )
            # Return a basic embed if there's an error
            return Embed(
                title="Planning",
                description="Erreur lors de la récupération du planning",
                color=Colors.ERROR,
                timestamp=datetime.now(pytz.UTC),
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
        elif offline:
            return await self.get_offline_stream_info(user_id)
        else:
            return self.get_online_stream_info(stream)

    def get_stream_info_from_data(self, data: ChannelUpdateEvent, offline, stream: Stream = None):
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
        description = f"Joue à **{stream.game_name}** pour **{stream.viewer_count}** viewers"
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
            # Return a basic embed without footer if there's an error
            return Embed(
                title=title,
                description=description,
                color=Colors.TWITCH,
                url=f"https://twitch.tv/{user_login}",
                timestamp=datetime.now(pytz.UTC),
            )

    async def edit_message(
        self,
        streamer: StreamerInfo,
        offline: bool = False,
        data: ChannelUpdateEvent | StreamOfflineEvent | None = None,
    ) -> None:
        """
        Update the planning message (if configured) and manage the scheduled
        Discord event for a streamer. The planning message is optional — when
        it isn't set up, scheduled events are still handled normally.
        """
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

    async def add_user(self, embed: Embed, user_id: str, offline: bool = False) -> Embed:
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

            if offline and hasattr(user, "offline_image_url") and user.offline_image_url:
                embed.set_image(url=f"{user.offline_image_url}?{datetime.now().timestamp()}")

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
        broadcaster_name = data.event.broadcaster_user_name
        logger.info("Stream is live: %s", broadcaster_name)

        # Find all streamers matching this user_id
        streamers = self.get_streamer_by_user_id(user_id)
        for streamer in streamers:
            try:
                # Get stream data to store session info
                stream = await self.get_stream_data(user_id)
                if stream:
                    streamer.stream_start_time = ensure_utc(stream.started_at)
                    streamer.stream_title = stream.title
                    streamer.stream_id = stream.id
                    streamer.stream_categories = [stream.game_name] if stream.game_name else []
                    logger.info(
                        f"Stored stream info for {streamer.streamer_id}: started at {streamer.stream_start_time}"
                    )

                await self.send_stream_start_notification(streamer, broadcaster_name, stream)

                # For each server tracking this streamer, update the message
                await self.edit_message(streamer, offline=False)
            except Exception as e:
                logger.error(
                    f"Error handling live start for {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                )

        for streamer_id in {s.streamer_id for s in streamers}:
            await self._save_streamer_state(streamer_id)

        self.update.reschedule(IntervalTrigger(minutes=15))

    async def on_live_end(self, data: StreamOfflineEvent):
        """
        Handle the event when a live stream ends.

        Args:
            data (StreamOfflineEvent): The event data.
        """
        user_id = data.event.broadcaster_user_id
        broadcaster_name = data.event.broadcaster_user_name
        logger.info("Stream is offline: %s", broadcaster_name)

        # Find all streamers matching this user_id
        streamers = self.get_streamer_by_user_id(user_id)
        for streamer in streamers:
            try:
                # Send stream end notification
                await self.send_stream_end_notification(streamer, broadcaster_name)

                # For each server tracking this streamer, update the message
                await self.edit_message(streamer, offline=True)

                # Clear stream session info
                streamer.stream_start_time = None
                streamer.stream_title = None
                streamer.stream_id = None
                streamer.stream_categories = []
                # Keep last_notified_title and last_notified_category to track changes even when offline
            except Exception as e:
                logger.error(
                    f"Error handling live end for {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                )

        for streamer_id in {s.streamer_id for s in streamers}:
            await self._save_streamer_state(streamer_id)

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

    async def send_stream_start_notification(
        self,
        streamer: StreamerInfo,
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

    async def send_stream_end_notification(self, streamer: StreamerInfo, broadcaster_name: str):
        """
        Send a notification message when a stream ends with stream summary.

        Args:
            streamer (StreamerInfo): The streamer info object.
            broadcaster_name (str): The broadcaster's display name.
        """
        if not streamer.notif_channel:
            return
        if not streamer.notify_stream_end:
            return

        try:
            # Calculate stream duration
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

            # Get VOD info if available
            vod_url = None
            try:
                videos = self.twitch.get_videos(
                    user_id=streamer.user_id, video_type="archive", first=1
                )
                async for video in videos:
                    # Check if this VOD is from the stream that just ended
                    if streamer.stream_id and video.stream_id == streamer.stream_id:
                        vod_url = video.url
                    break
            except Exception as e:
                logger.debug(f"Could not fetch VOD for {streamer.streamer_id}: {e}")

            # Create embed for stream end notification
            title = self.get_display_value(streamer.stream_title, "Titre non renseigné")

            # Build the list of categories played during the session (deduplicated while preserving order)
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

            # Get user info for thumbnail
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
        raw_category = (data.event.category_name or "").strip()
        for streamer in streamers:
            try:
                stream = await self.get_stream_data(user_id)

                # Track category changes during the live session
                if stream is not None and raw_category:
                    if (
                        not streamer.stream_categories
                        or streamer.stream_categories[-1] != raw_category
                    ):
                        streamer.stream_categories.append(raw_category)

                # Check if title or category actually changed to avoid duplicate notifications
                new_title = self.get_display_value(data.event.title, "Titre non renseigné")
                new_category = self.get_display_value(
                    data.event.category_name, "Catégorie non renseignée"
                )

                title_changed = streamer.last_notified_title != new_title
                category_changed = streamer.last_notified_category != new_category

                # Send notification only if something actually changed
                if (
                    streamer.notif_channel
                    and streamer.notify_stream_update
                    and (title_changed or category_changed)
                ):
                    # Update the last notified values
                    streamer.last_notified_title = new_title
                    streamer.last_notified_category = new_category

                    update_embed = await self.create_notification_embed(
                        streamer.guild_id,
                        title="Mise à jour du live Twitch",
                        description=f"{user_name} a modifié les informations du live.",
                    )
                    update_embed.add_field(name="Titre", value=new_title, inline=False)
                    update_embed.add_field(name="Catégorie", value=new_category, inline=False)
                    if not stream:
                        update_embed.add_field(
                            name="Statut",
                            value="Modification détectée hors live",
                            inline=False,
                        )

                    await streamer.notif_channel.send(embed=update_embed)
                elif not (title_changed or category_changed):
                    logger.debug(
                        f"Skipping duplicate notification for {streamer.streamer_id} - title and category unchanged"
                    )

                # Update the message
                await self.edit_message(streamer, offline=(stream is None), data=data)
            except Exception as e:
                logger.error(
                    f"Error handling update for {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                )

        for streamer_id in {s.streamer_id for s in streamers}:
            await self._save_streamer_state(streamer_id)

        # Check if any streamer is live to reschedule update task
        stream_checks = []
        for streamer in self.streamers.values():
            if streamer.user_id:
                stream_data = await self.get_stream_data(streamer.user_id)
                stream_checks.append(stream_data is not None)

        if any(stream_checks):
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
                    logger.error(
                        f"Error updating streamer {streamer.streamer_id} in guild {streamer.guild_id}: {e}"
                    )

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, utc=False) for i in range(24)]))
    async def check_new_emotes(self):
        logger.debug("Checking new emotes")

        # Group streamers by streamer_id so we only fetch emotes once per unique streamer
        # and send notifications to all guilds following that streamer.
        streamers_by_id: dict[str, list[StreamerInfo]] = {}
        for streamer in self.streamers.values():
            if not streamer.user_id or not streamer.notif_channel:
                continue
            streamers_by_id.setdefault(streamer.streamer_id, []).append(streamer)

        for streamer_id, guild_streamers in streamers_by_id.items():
            # Use the first streamer's user_id (same for all entries of this streamer)
            user_id = guild_streamers[0].user_id

            try:
                emotes = await self.twitch.get_channel_emotes(user_id)
                emote_col = mongo_manager.get_global_collection(f"twitch_emotes_{streamer_id}")

                # Load existing emotes from MongoDB (global, shared across guilds)
                # Data structure: {emote_id: {"name": str, "cached_file": str (optional)}}
                data = {}
                try:
                    async for doc in emote_col.find():
                        emote_id = doc["_id"]
                        data[emote_id] = {
                            "name": doc.get("name", ""),
                            "cached_file": doc.get("cached_file"),
                        }
                except Exception as e:
                    logger.error(f"Error loading emotes from MongoDB for {streamer_id}: {e}")

                # Check for new and deleted emotes
                new_emotes = [emote for emote in emotes if emote.id not in data]
                emote_ids = [emote.id for emote in emotes]
                deleted_emotes = [emote_id for emote_id in data if emote_id not in emote_ids]

                # If the DB was empty (initial sync after migration), skip all notifications
                # and just populate the database silently.
                if not data and new_emotes:
                    logger.warning(
                        "Initial emote sync for %s: %d emotes found – skipping notifications",
                        streamer_id,
                        len(new_emotes),
                    )
                    docs = []
                    for emote in emotes:
                        image_url = emote.images.get(
                            "url_4x", emote.images.get("url_2x", emote.images.get("url_1x"))
                        )
                        cached_file = await self.download_emote_image(
                            emote.id, image_url, streamer_id
                        )
                        docs.append(
                            {"_id": emote.id, "name": emote.name, "cached_file": cached_file}
                        )
                    if docs:
                        await emote_col.insert_many(docs)
                    continue

                # Build a map of current emote names for replacement detection
                current_emote_names = {emote.name: emote for emote in emotes}
                deleted_emote_names = {
                    data[emote_id]["name"]: emote_id for emote_id in deleted_emotes
                }

                # Detect replaced emotes (same name, different ID)
                replaced_emotes = []
                truly_new_emotes = []
                for emote in new_emotes:
                    if emote.name in deleted_emote_names:
                        replaced_emotes.append((deleted_emote_names[emote.name], emote))
                    else:
                        truly_new_emotes.append(emote)

                # Remove replaced emotes from deleted list
                truly_deleted_emotes = [
                    emote_id
                    for emote_id in deleted_emotes
                    if data[emote_id]["name"] not in current_emote_names
                ]

                # Process replaced emotes (same name, new ID)
                if replaced_emotes:
                    logger.info(f"Replaced emotes found for {streamer_id}")
                    for old_emote_id, new_emote in replaced_emotes:
                        old_cached_file = self.get_cached_emote_path(old_emote_id, streamer_id)
                        new_image_url = new_emote.images.get(
                            "url_4x", new_emote.images.get("url_2x", new_emote.images.get("url_1x"))
                        )

                        logger.info(f"Replaced emote for {streamer_id}: {new_emote.name}")

                        # Send notification to all guilds following this streamer
                        for streamer in guild_streamers:
                            if not streamer.notify_emote_changes:
                                continue
                            embed = await self.create_notification_embed(
                                streamer.guild_id,
                                title="Mise à jour d'emote",
                                description=f"L'emote **{new_emote.name}** a été remplacé sur la chaîne de **{streamer_id}**.",
                                color=Colors.ORANGE,
                            )
                            embed.add_field(
                                name="Emote",
                                value=self.get_display_value(new_emote.name),
                                inline=True,
                            )
                            embed.add_field(
                                name="Streamer",
                                value=self.get_display_value(streamer_id),
                                inline=True,
                            )
                            embed.add_field(name="Action", value="Remplacement", inline=True)
                            if new_image_url:
                                embed.set_thumbnail(url=new_image_url)

                            if old_cached_file:
                                embed.add_field(
                                    name="Ancienne version", value="Image jointe", inline=True
                                )
                                embed.add_field(
                                    name="Nouvelle version",
                                    value="Thumbnail de l'embed",
                                    inline=True,
                                )
                                await streamer.notif_channel.send(
                                    embed=embed,
                                    files=[
                                        File(old_cached_file, file_name=f"old_{new_emote.name}.png")
                                    ],
                                )
                            else:
                                embed.add_field(
                                    name="Nouvelle version",
                                    value="Thumbnail de l'embed",
                                    inline=True,
                                )
                                await streamer.notif_channel.send(embed=embed)

                        # Delete old cached file and download new one (once, globally)
                        if old_cached_file:
                            self.delete_cached_emote(old_emote_id, streamer_id)

                        new_cached_file = await self.download_emote_image(
                            new_emote.id, new_image_url, streamer_id
                        )

                        # Update data with new emote info
                        del data[old_emote_id]
                        data[new_emote.id] = {
                            "name": new_emote.name,
                            "cached_file": new_cached_file,
                        }

                # Process truly new emotes
                if truly_new_emotes:
                    logger.debug(f"New emotes found for {streamer_id}")
                    for emote in truly_new_emotes:
                        image_url = emote.images.get(
                            "url_4x", emote.images.get("url_2x", emote.images.get("url_1x"))
                        )

                        # Download and cache the emote image (once, globally)
                        cached_file = await self.download_emote_image(
                            emote.id, image_url, streamer_id
                        )
                        data[emote.id] = {"name": emote.name, "cached_file": cached_file}

                        details = self.get_emote_details(emote)

                        logger.info(f"New emote for {streamer_id}: {emote.name}")

                        # Send notification to all guilds following this streamer
                        for streamer in guild_streamers:
                            if not streamer.notify_emote_changes:
                                continue
                            embed = await self.create_notification_embed(
                                streamer.guild_id,
                                title="Nouvel emote ajouté",
                                description=f"Un nouvel emote est disponible sur la chaine de **{streamer_id}**.",
                            )
                            embed.add_field(
                                name="Emote", value=self.get_display_value(emote.name), inline=True
                            )
                            embed.add_field(
                                name="Streamer",
                                value=self.get_display_value(streamer_id),
                                inline=True,
                            )
                            embed.add_field(name="Type", value=details, inline=True)
                            if image_url:
                                embed.set_thumbnail(url=image_url)
                            await streamer.notif_channel.send(embed=embed)

                # Process truly deleted emotes
                if truly_deleted_emotes:
                    logger.info(f"Deleted emotes found for {streamer_id}")
                    for emote_id in truly_deleted_emotes:
                        emote_data = data[emote_id]
                        emote_name = emote_data["name"]
                        cached_file = self.get_cached_emote_path(emote_id, streamer_id)

                        logger.info(f"Deleted emote for {streamer_id}: {emote_name}")

                        # Send notification to all guilds following this streamer
                        for streamer in guild_streamers:
                            if not streamer.notify_emote_changes:
                                continue
                            embed = await self.create_notification_embed(
                                streamer.guild_id,
                                title="Emote supprimé",
                                description=f"L'emote **{emote_name}** a été retiré de la chaîne de **{streamer_id}**.",
                            )
                            embed.add_field(
                                name="Emote", value=self.get_display_value(emote_name), inline=True
                            )
                            embed.add_field(
                                name="Streamer",
                                value=self.get_display_value(streamer_id),
                                inline=True,
                            )
                            embed.add_field(name="Action", value="Suppression", inline=True)

                            if cached_file:
                                await streamer.notif_channel.send(
                                    embed=embed,
                                    files=[File(cached_file, file_name=f"{emote_name}.png")],
                                )
                            else:
                                await streamer.notif_channel.send(embed=embed)

                        # Delete cached file after sending to all guilds (once, globally)
                        if cached_file:
                            self.delete_cached_emote(emote_id, streamer_id)

                        del data[emote_id]

                # Download and cache images for existing emotes that don't have cached files
                for emote in emotes:
                    if emote.id in data and not self.get_cached_emote_path(emote.id, streamer_id):
                        image_url = emote.images.get(
                            "url_4x", emote.images.get("url_2x", emote.images.get("url_1x"))
                        )
                        cached_file = await self.download_emote_image(
                            emote.id, image_url, streamer_id
                        )
                        data[emote.id]["cached_file"] = cached_file

                # Save updated emotes to MongoDB
                # Also save when cached files were updated for existing emotes
                has_cache_updates = any(
                    emote.id in data and data[emote.id].get("cached_file") is not None
                    for emote in emotes
                    if emote.id in data
                )
                if truly_new_emotes or truly_deleted_emotes or replaced_emotes or has_cache_updates:
                    # Sync MongoDB with current data dict
                    await emote_col.delete_many({})
                    if data:
                        docs = [
                            {
                                "_id": eid,
                                "name": edata["name"],
                                "cached_file": edata.get("cached_file"),
                            }
                            for eid, edata in data.items()
                        ]
                        await emote_col.insert_many(docs)

            except Exception as e:
                logger.error(f"Error checking emotes for {streamer_id}: {e}")

    @slash_command(
        name="twitch-planning-setup",
        description="Créer/attacher le message de planning Twitch d'un streamer",
    )
    @slash_option(
        name="streamer",
        description="ID du streamer configuré",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="channel",
        description="Canal où créer le message de planning",
        opt_type=OptionType.CHANNEL,
        required=True,
        channel_types=[ChannelType.GUILD_TEXT, ChannelType.GUILD_NEWS],
    )
    @slash_option(
        name="pin",
        description="Épingler le message",
        opt_type=OptionType.BOOLEAN,
        required=False,
    )
    @slash_default_member_permission(Permissions.MANAGE_GUILD)
    async def twitch_planning_setup(
        self,
        ctx: SlashContext,
        streamer: str,
        channel: BaseChannel,
        pin: bool = False,
    ):
        if not ctx.guild:
            await send_error(ctx, "Commande utilisable uniquement dans un serveur.")
            return
        streamer_key = self.get_streamer_key(int(ctx.guild.id), streamer)
        streamer_info = self.streamers.get(streamer_key)
        if streamer_info is None:
            await send_error(ctx, f"Streamer '{streamer}' non trouvé pour ce serveur.")
            return
        msg = await channel.send(f"Initialisation du planning de {streamer}…")
        if pin:
            try:
                await msg.pin()
            except Exception as e:
                logger.warning("Could not pin planning message: %s", e)
        _save_streamer_channel_message(
            str(ctx.guild.id), streamer, str(channel.id), str(msg.id), pin
        )
        streamer_info.planning_channel_id = int(channel.id)
        streamer_info.planning_message_id = int(msg.id)
        streamer_info.planning_pin = pin
        streamer_info.channel = channel
        streamer_info.message = msg
        await send_success(
            ctx,
            f"Message de planning créé pour **{streamer}** dans {channel.mention}{' et épinglé' if pin else ''}.",
        )

    @twitch_planning_setup.autocomplete("streamer")
    async def twitch_planning_setup_streamer_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return
        guild_id = int(ctx.guild.id)
        query = (ctx.input_text or "").lower()
        choices = [
            {"name": s.streamer_id, "value": s.streamer_id}
            for s in self.streamers.values()
            if s.guild_id == guild_id and query in s.streamer_id.lower()
        ][:25]
        await ctx.send(choices=choices)
