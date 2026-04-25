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
    youtubeChannelList: list[str] = ui(
        "Chaînes YouTube suivies",
        "list",
        required=True,
        description=(
            "Liste des handles YouTube à surveiller (un par ligne). "
            "Format accepté : `@handle` ou juste le handle. "
            "Modifiable directement depuis le dashboard."
        ),
    )
    youtubeChannelLabels: dict[str, str] = ui(
        "Libellés personnalisés",
        "keyvaluemap",
        description=(
            "Optionnel : associe un nom d'affichage à chaque handle. "
            "Utilisé dans les logs et messages."
        ),
        key_label="Handle",
        value_label="Libellé",
    )
    youtubeIncludeShorts: bool = ui(
        "Shorts (défaut)",
        "boolean",
        default=False,
        description=(
            "Valeur par défaut pour inclure les Shorts. "
            "Surchargeable par chaîne via « Shorts par chaîne »."
        ),
    )
    youtubeShortsPerChannel: dict[str, str] = ui(
        "Shorts par chaîne",
        "keyvaluemap",
        description=(
            "Override par handle : `true` pour notifier les Shorts de cette "
            "chaîne, `false` pour les ignorer. Une entrée absente utilise la "
            "valeur par défaut ci-dessus."
        ),
        key_label="Handle",
        value_label="true / false",
    )
    youtubeIncludeLive: bool = ui(
        "Notifier les lives",
        "boolean",
        default=False,
        description="Inclure les diffusions en direct (live broadcasts).",
    )
    youtubeIncludeVod: bool = ui(
        "Notifier les VOD",
        "boolean",
        default=True,
        description="Inclure les vidéos longues classiques (VOD).",
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
            labels: dict[str, str] = srv_config.get("youtubeChannelLabels") or {}
            for user in srv_config["youtubeChannelList"]:
                if not user:
                    continue
                # Strip leading "@" so @handle and bare handle both resolve.
                handle = user.lstrip("@")
                uploads = await self.get_uploads(handle)
                video_id = await self.get_video_id(uploads)
                if self.is_video_already_checked(server, user, video_id, youtube_data):
                    continue
                youtube_data = self.update_youtube_data(server, user, video_id, youtube_data)
                filters = self._content_filters(srv_config, handle)
                if not is_initial_sync and await self.is_video_valid(video_id, filters):
                    label = labels.get(handle) or labels.get(user) or handle
                    try:
                        rendered = template.format(video_id=video_id, handle=handle, label=label)
                    except (KeyError, IndexError):
                        rendered = f"https://www.youtube.com/watch?v={video_id}"
                    await channel.send(rendered)
            await self.save_youtube_data(youtube_data)

    @staticmethod
    def _content_filters(srv_config: dict, handle: str) -> dict[str, object]:
        """Per-channel content filter dict consumed by ``is_video_valid``.

        Falls back to the guild-wide defaults when the channel has no override.
        """
        try:
            short_max = int(srv_config.get("youtubeShortMaxSeconds", 90))
        except (TypeError, ValueError):
            short_max = 90

        shorts_default = bool(srv_config.get("youtubeIncludeShorts", False))
        per_channel = srv_config.get("youtubeShortsPerChannel") or {}
        # Accept either bare or @-prefixed keys so the operator's UI input matches
        # whatever they typed in `youtubeChannelList`.
        raw = per_channel.get(handle, per_channel.get(f"@{handle}"))
        if raw is None:
            shorts = shorts_default
        else:
            shorts = str(raw).strip().lower() in {"1", "true", "yes", "y", "oui", "on"}

        return {
            "shorts": shorts,
            "live": bool(srv_config.get("youtubeIncludeLive", False)),
            "vod": bool(srv_config.get("youtubeIncludeVod", True)),
            "short_max_seconds": max(1, short_max),
        }

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
