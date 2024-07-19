import datetime
import json
import os

import aiohttp
import isodate
from interactions import BaseChannel, Client, Extension, IntervalTrigger, Task, listen

from src import logutil
from src.utils import load_config, fetch

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleYoutube")

YOUTUBE_API_KEY = config["youtube"]["youtubeApiKey"]
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"


class YoutubeClass(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.playlist_cache = {}  # Add a cache for playlists

    @listen()
    async def on_startup(self):
        self.check_youtube.start()
        # await self.check_youtube()

    @Task.create(IntervalTrigger(minutes=5))
    async def check_youtube(self):
        for server in enabled_servers:
            if module_config[str(server)].get("ChannelId"):
                channel: BaseChannel = await self.bot.fetch_channel(
                    module_config[str(server)].get("ChannelId")
                )
            else:
                continue
            for user in module_config[str(server)]["youtubeChannelList"]:
                uploads = await self.get_uploads(user)
                video_id = await self.get_video_id(uploads)
                youtube_data = self.get_youtube_data()
                if self.is_video_already_checked(server, user, video_id, youtube_data):
                    continue
                youtube_data = self.update_youtube_data(
                    server, user, video_id, youtube_data
                )
                if await self.is_video_valid(video_id):
                    await channel.send(f"https://www.youtube.com/watch?v={video_id}")
                self.save_youtube_data(youtube_data)

    async def get_uploads(self, user):
        if user not in self.playlist_cache:
            url = f"{YOUTUBE_API_URL}/channels?part=contentDetails&forHandle={user}&key={YOUTUBE_API_KEY}"
            data = await fetch(url, return_type='json')
            logger.debug(data)
            uploads = data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            self.playlist_cache[user] = uploads
        else:
            uploads = self.playlist_cache[user]
        return uploads

    async def get_video_id(self, uploads):
        url = f"{YOUTUBE_API_URL}/playlistItems?part=snippet&maxResults=1&playlistId={uploads}&key={YOUTUBE_API_KEY}"
        data = await fetch(url, return_type='json')
        logger.debug(data)
        return data["items"][0]["snippet"]["resourceId"]["videoId"]

    def get_youtube_data(self):
        try:
            with open("data/youtube.json", "r", encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            return {}

    def is_video_already_checked(self, server, user, video_id, youtube_data):
        return (
            str(server) in youtube_data
            and user in youtube_data[str(server)]
            and youtube_data[str(server)][user] == video_id
        )

    def update_youtube_data(self, server, user, video_id, youtube_data):
        youtube_data[str(server)] = youtube_data.get(str(server), {})
        youtube_data[str(server)][user] = video_id
        return youtube_data

    async def is_video_valid(self, video_id):
        url = f"{YOUTUBE_API_URL}/videos?part=snippet,contentDetails&id={video_id}&key={YOUTUBE_API_KEY}"
        data = await fetch(url, return_type='json')
        logger.debug(data)
        if data["items"][0]["snippet"]["liveBroadcastContent"] == "none":
            duration = isodate.parse_duration(
                data["items"][0]["contentDetails"]["duration"]
            )
            if duration > datetime.timedelta(minutes=1, seconds=30):
                return True
            else:
                logger.info("New video is a Short")
        else:
            logger.info("New video is a live stream")
        return False

    def save_youtube_data(self, youtube_data):
        with open("data/youtube.json", "w", encoding="utf-8") as file:
            json.dump(youtube_data, file, indent=4)
