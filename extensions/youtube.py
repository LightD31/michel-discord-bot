"""Extension YouTube — notifications automatiques de nouvelles vidéos."""

import datetime
import os

import aiohttp
import isodate
from interactions import BaseChannel, Client, Extension, IntervalTrigger, Task, listen

from src.core import logging as logutil
from src.core.config import load_config
from src.core.db import mongo_manager
from src.core.http import fetch
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleYoutube")
class YoutubeConfig(SchemaBase):
    __label__ = "YouTube"
    __description__ = "Notifications de nouvelles vidéos YouTube."
    __icon__ = "▶️"
    __category__ = "Médias & Streaming"

    enabled: bool = enabled_field()
    ChannelId: str = ui(
        "Salon de notification",
        "channel",
        required=True,
        description="Salon où sont publiées les notifications de nouvelles vidéos.",
    )
    youtubeChannelList: dict[str, dict] = ui(
        "Chaînes YouTube suivies",
        "youtubechannelmap",
        required=True,
        description=(
            "Une ligne par chaîne avec ses propres bascules Shorts / Lives / "
            "VOD. Le handle accepte `@handle` ou juste le handle ; un libellé "
            "optionnel apparaît dans les logs et les notifications."
        ),
    )
    youtubeShortMaxSeconds: int = ui(
        "Seuil Short (secondes)",
        "number",
        default=90,
        description=(
            "Durée maximale (en secondes) en deçà de laquelle une vidéo est "
            "considérée comme un Short. 90 par défaut."
        ),
    )
    youtubeNotificationTemplate: str = ui(
        "Modèle de notification",
        "string",
        default="https://www.youtube.com/watch?v={video_id}",
        description=(
            "Texte envoyé pour chaque nouvelle vidéo. Variables : "
            "`{video_id}`, `{handle}`, `{label}`."
        ),
    )


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleYoutube")

YOUTUBE_API_KEY = config["youtube"]["youtubeApiKey"]
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3"


class YoutubeExtension(Extension):
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
            srv_config = module_config[str(server)]
            if srv_config.get("ChannelId"):
                channel: BaseChannel = await self.bot.fetch_channel(srv_config.get("ChannelId"))
            else:
                continue
            youtube_data = await self.get_youtube_data()
            is_initial_sync = str(server) not in youtube_data
            if is_initial_sync:
                logger.warning(
                    "Initial YouTube sync for server %s – skipping notifications",
                    server,
                )
            template = srv_config.get(
                "youtubeNotificationTemplate", "https://www.youtube.com/watch?v={video_id}"
            )
            try:
                short_max = int(srv_config.get("youtubeShortMaxSeconds", 90))
            except (TypeError, ValueError):
                short_max = 90
            short_max = max(1, short_max)

            for raw_handle, channel_cfg in self._iter_channels(srv_config):
                if not raw_handle:
                    continue
                handle = raw_handle.lstrip("@")
                uploads = await self.get_uploads(handle)
                video_id = await self.get_video_id(uploads)
                if self.is_video_already_checked(server, raw_handle, video_id, youtube_data):
                    continue
                youtube_data = self.update_youtube_data(server, raw_handle, video_id, youtube_data)
                filters = {
                    "shorts": bool(channel_cfg.get("shorts", False)),
                    "live": bool(channel_cfg.get("live", False)),
                    "vod": bool(channel_cfg.get("vod", True)),
                    "short_max_seconds": short_max,
                }
                if not is_initial_sync and await self.is_video_valid(video_id, filters):
                    label = channel_cfg.get("label") or handle
                    try:
                        rendered = template.format(video_id=video_id, handle=handle, label=label)
                    except (KeyError, IndexError):
                        rendered = f"https://www.youtube.com/watch?v={video_id}"
                    await channel.send(rendered)
            await self.save_youtube_data(youtube_data)

    @staticmethod
    def _iter_channels(srv_config: dict):
        """Yield ``(handle, channel_cfg)`` pairs from the configured channel list.

        Accepts both the structured dict shape (``youtubechannelmap``) and the
        legacy ``list[str]`` shape — list entries default to VOD-only with
        Shorts and Lives disabled, matching the previous behaviour.
        """
        raw = srv_config.get("youtubeChannelList")
        if isinstance(raw, dict):
            for handle, cfg in raw.items():
                yield handle, (cfg if isinstance(cfg, dict) else {})
        elif isinstance(raw, list):
            for handle in raw:
                yield handle, {"shorts": False, "live": False, "vod": True}

    async def get_uploads(self, user):
        if user not in self.playlist_cache:
            url = f"{YOUTUBE_API_URL}/channels?part=contentDetails&forHandle={user}&key={YOUTUBE_API_KEY}"
            data = await fetch(url, return_type="json")
            logger.debug(data)
            uploads = data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            self.playlist_cache[user] = uploads
        else:
            uploads = self.playlist_cache[user]
        return uploads

    async def get_video_id(self, uploads):
        url = f"{YOUTUBE_API_URL}/playlistItems?part=snippet&maxResults=1&playlistId={uploads}&key={YOUTUBE_API_KEY}"
        data = await fetch(url, return_type="json")
        logger.debug(data)
        return data["items"][0]["snippet"]["resourceId"]["videoId"]

    async def get_youtube_data(self):
        data = {}
        for server_id in enabled_servers:
            col = mongo_manager.get_guild_collection(server_id, "youtube")
            doc = await col.find_one({"_id": "youtube_data"})
            if doc:
                data[str(server_id)] = {k: v for k, v in doc.items() if k != "_id"}
        return data

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

    async def is_video_valid(self, video_id, filters: dict[str, object] | None = None):
        """Decide whether to surface ``video_id`` based on per-guild filters.

        ``filters`` keys: ``shorts``, ``live``, ``vod``, ``short_max_seconds``.
        Default mirrors the legacy behaviour (VOD-only, 90 s short threshold).
        """
        if filters is None:
            filters = {"shorts": False, "live": False, "vod": True, "short_max_seconds": 90}
        url = f"{YOUTUBE_API_URL}/videos?part=snippet,contentDetails&id={video_id}&key={YOUTUBE_API_KEY}"
        data = await fetch(url, return_type="json")
        logger.debug(data)
        item = data["items"][0]
        live_state = item["snippet"]["liveBroadcastContent"]
        if live_state != "none":
            if filters["live"]:
                return True
            logger.info("New video is a live stream — skipped (live filter off)")
            return False

        duration = isodate.parse_duration(item["contentDetails"]["duration"])
        threshold = datetime.timedelta(seconds=int(filters.get("short_max_seconds", 90)))
        is_short = duration <= threshold
        if is_short:
            if filters["shorts"]:
                return True
            logger.info("New video is a Short — skipped (shorts filter off)")
            return False
        if filters["vod"]:
            return True
        logger.info("New video is a VOD — skipped (vod filter off)")
        return False

    async def save_youtube_data(self, youtube_data):
        for server_id, users in youtube_data.items():
            col = mongo_manager.get_guild_collection(server_id, "youtube")
            await col.update_one({"_id": "youtube_data"}, {"$set": users}, upsert=True)
