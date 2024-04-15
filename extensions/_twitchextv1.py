import datetime
import os

import requests
from dotenv import load_dotenv, set_key
from interactions import (
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

from src import logutil

TWITCH_USER_ID = "41719107"


class TwitchExt(Extension):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logutil.init_logger(os.path.basename(__file__))
        load_dotenv()
        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.access_token = os.getenv("TWITCH_ACCESS_TOKEN")
        self.refresh_token = os.getenv("TWITCH_REFRESH_TOKEN")
        self.planning_channel_id = int(os.getenv("TWITCH_PLANNING_CHANNEL_ID"))
        self.planning_message_id = int(os.getenv("TWITCH_PLANNING_MESSAGE_ID"))
        self.refresh_time = os.getenv("TWITCH_REFRESH_TIME")

    @listen()
    async def on_startup(self):
        self.schedule.start()

    @Task.create(IntervalTrigger(seconds=30))
    async def schedule(self):
        try:
            self.refresh_token_if_needed()
            channel = await self.bot.fetch_channel(self.planning_channel_id)
            message: Message = await channel.fetch_message(self.planning_message_id)
            liveembed = self.check_if_live()
            embed = self.fetch_schedule()
            if liveembed is not None:
                await message.edit(content="", embed=[embed, liveembed])
            else:
                await message.edit(content="", embed=embed)
        except Exception as e:
            self.logger.error(f"Error in schedule task: {e}")

    def refresh_token_if_needed(self):
        if self.refresh_time is None or datetime.datetime.fromisoformat(
            self.refresh_time
        ) < datetime.datetime.now() - datetime.timedelta(days=30):
            url = f"https://twitchtokengenerator.com/api/refresh/{self.refresh_token}"
            r = requests.post(url, timeout=5)
            if r.status_code == 200:
                self.access_token = r.json()["token"]
                os.environ["TWITCH_ACCESS_TOKEN"] = self.access_token
                set_key(".env", "TWITCH_ACCESS_TOKEN", self.access_token)
                set_key(
                    ".env", "TWITCH_REFRESH_TIME", datetime.datetime.now().isoformat()
                )
            else:
                self.logger.error("Error while refreshing Twitch access token")

    def check_if_live(self):
        # checks if stream is live
        url = f"https://api.twitch.tv/helix/streams?user_id={TWITCH_USER_ID}"
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200 and len(r.json()["data"]) > 0:
            liveembed = Embed(
                title=r.json()["data"][0]["title"],
                description=f"Joue à **{r.json()['data'][0]['game_name']}** pour **{r.json()['data'][0]['viewer_count']}** viewers",
                color=0x6441A5,
                url=f"https://twitch.tv/{r.json()['data'][0]['user_login']}",
                footer="MICHEL > Streamcord (Keur erlen)",
                timestamp=datetime.datetime.now(),
            )
            liveembed.set_image(
                url=r.json()["data"][0]["thumbnail_url"].format(width=1280, height=720)
            )
            # Get user profile picture
            url = f"https://api.twitch.tv/helix/users?id={TWITCH_USER_ID}"
            r = requests.get(url, headers=headers, timeout=5)
            if r.status_code == 200:
                liveembed.set_author(
                    name=f"{r.json()['data'][0]['display_name']} est en live !",
                    icon_url=r.json()["data"][0]["profile_image_url"],
                    url=f"https://twitch.tv/{r.json()['data'][0]['login']}",
                )
            else:
                self.logger.error(
                    "Error while fetching Twitch user infos\n%s\n%s", r.text, r.url
                )
            return liveembed
        # reads channel infos
        url = f"https://api.twitch.tv/helix/channels?broadcaster_id={TWITCH_USER_ID}"
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            liveembed = Embed(
                title=f"{r.json()['data'][0]['broadcaster_name']} n'est pas en live",
                description=f"Titre : **{r.json()['data'][0]['title']}**\nCatégorie : **{r.json()['data'][0]['game_name']}**\n[Rejoindre la chaîne](https://twitch.tv/{r.json()['data'][0]['broadcaster_login']})",
                color=0x6441A5,
            )
            return liveembed
        self.logger.error(
            "Error while fetching Twitch channel infos\n%s\n%s", r.text, r.url
        )

        return None

    def fetch_schedule(self):
        url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={TWITCH_USER_ID}&first=5&start_time={(datetime.datetime.now()-datetime.timedelta(hours=2)).isoformat(timespec='seconds')}Z"
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            schedule = r.json()["data"]["segments"]
            embed = Embed(
                title=f"<:TeamBelieve:808056449750138880> Planning de {r.json()['data']['broadcaster_name']} <:TeamBelieve:808056449750138880>",
                description="Les 5 prochains streams (planifiés)",
                color=0x6441A5,
                timestamp=datetime.datetime.now(),
                footer=EmbedFooter(text="MICHEL LE ROBOT"),
            )
            for _, stream in enumerate(schedule):
                embed.add_field(
                    name=f"{stream['title']}",
                    value=f"{stream['category']['name']}\n{utils.timestamp_converter(stream['start_time']).format(TimestampStyles.LongDate)} ({utils.timestamp_converter(stream['start_time']).format(TimestampStyles.ShortTime)}-{utils.timestamp_converter(stream['end_time']).format(TimestampStyles.ShortTime)})",
                    inline=False,
                )
            return embed
        else:
            self.logger.error(
                "Error while fetching Twitch schedule\n%s\n%s", r.text, r.url
            )
            return None
