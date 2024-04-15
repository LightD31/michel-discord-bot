import datetime
import os

from dotenv import load_dotenv
from interactions import (
    Client,
    Embed,
    EmbedFooter,
    Extension,
    IntervalTrigger,
    Message,
    Task,
    TimestampStyles,
    listen,
    utils,
)
from twitchAPI.helper import first
from twitchAPI.object.api import (
    ChannelInformation,
    ChannelStreamSchedule,
    Stream,
    TwitchUser,
)
from twitchAPI.twitch import Twitch

from src import logutil

TWITCH_USER_ID = "41719107"
TWITCH_COLOR = 0x6441A5


class TwitchExt(Extension):
    """Twitch Extension for bot."""

    def __init__(self, bot: Client):
        """Initialize the Twitch Extension."""
        self.bot: Client = bot
        self.logger = logutil.init_logger(os.path.basename(__file__))
        load_dotenv()
        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        self.planning_channel_id = int(os.getenv("TWITCH_PLANNING_CHANNEL_ID"))
        self.planning_message_id = int(os.getenv("TWITCH_PLANNING_MESSAGE_ID"))
        self.twitch = None

    @listen()
    async def on_startup(self):
        """Start the Twitch Extension."""
        self.twitch = await Twitch(self.client_id, self.client_secret)
        self.schedule.start()

    @Task.create(IntervalTrigger(seconds=30))
    async def schedule(self):
        """Schedule the Twitch Extension."""
        try:
            channel = await self.bot.fetch_channel(self.planning_channel_id)
            message: Message = await channel.fetch_message(self.planning_message_id)
            liveembed = await self.check_if_live()
            embed = await self.fetch_schedule()
            if liveembed is not None:
                await message.edit(content="", embed=[embed, liveembed])
            else:
                await message.edit(content="", embed=embed)
        except Exception as e:
            self.logger.error(f"Error in schedule task: {e}. Retrying...")

    async def check_if_live(self):
        """Check if the stream is live."""
        # checks if stream is live
        stream: Stream = await first(self.twitch.get_streams(user_id=[TWITCH_USER_ID]))
        if stream is not None:
            liveembed = Embed(
                title=stream.title,
                description=f"Joue à **{stream.game_name}** pour **{stream.viewer_count}** viewers",
                color=TWITCH_COLOR,
                url=f"https://twitch.tv/{stream.user_login}",
                footer="MICHEL > Streamcord (Keur erlen)",
                timestamp=datetime.datetime.now(),
            )
            liveembed.set_image(url=stream.thumbnail_url.format(width=1280, height=720))
            # Get user profile picture
            user: TwitchUser = await first(
                self.twitch.get_users(user_ids=[TWITCH_USER_ID])
            )
            if user is not None:
                liveembed.set_author(
                    name=f"{user.display_name} est en live !",
                    icon_url=user.profile_image_url,
                    url=f"https://twitch.tv/{user.login}",
                )
            else:
                self.logger.error("Error while fetching Twitch user infos. Retrying...")
            return liveembed
        # reads channel infos
        channelinfos = await self.twitch.get_channel_information(
            broadcaster_id=TWITCH_USER_ID
        )
        channel: ChannelInformation = channelinfos[0]
        if channel is not None:
            liveembed = Embed(
                title=f"{channel.broadcaster_name} n'est pas en live",
                description=f"Titre : **{channel.title}**\nCatégorie : **{channel.game_name}**\n[Rejoindre la chaîne](https://twitch.tv/{channel.broadcaster_login})",
                color=TWITCH_COLOR,
            )
            return liveembed
        self.logger.error("Error while fetching Twitch channel infos. Retrying...")

        return None

    async def fetch_schedule(self):
        """Fetch the schedule of the Twitch Extension."""
        schedule: ChannelStreamSchedule = await self.twitch.get_channel_stream_schedule(
            broadcaster_id=TWITCH_USER_ID,
            first=5,
        )
        if schedule is not None:
            segments = schedule.segments
            embed = Embed(
                title=f"<:TeamBelieve:808056449750138880> Planning de {schedule.broadcaster_name} <:TeamBelieve:808056449750138880>",
                description="Les 5 prochains streams (planifiés)",
                color=TWITCH_COLOR,
                timestamp=datetime.datetime.now(),
                footer=EmbedFooter(text="MICHEL LE ROBOT"),
            )
            for _, stream in enumerate(segments):
                embed.add_field(
                    name=f"{stream.title}",
                    value=f"{stream.category.name}\n{utils.timestamp_converter(stream.start_time).format(TimestampStyles.LongDate)} ({utils.timestamp_converter(stream.start_time).format(TimestampStyles.ShortTime)}-{utils.timestamp_converter(stream.end_time).format(TimestampStyles.ShortTime)})",
                    inline=False,
                )
            return embed
        else:
            return None
