"""Extension YouTube — notifications automatiques de nouvelles vidéos."""

import datetime
import os

import aiohttp
import isodate
from interactions import BaseChannel, Client, Extension, IntervalTrigger, Task, listen

from features.links import shorten_text
from features.youtube import YoutubeRepository
from src.core import logging as logutil
from src.core.config import load_config
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


logger = logutil.init_logger(__name__)
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
        # One read of every guild's last-seen videos per tick, not one per guild.
        youtube_data = await self.get_youtube_data()
        for server in enabled_servers:
            try:
                await self._check_server(str(server), youtube_data)
            except Exception as e:  # noqa: BLE001 — one guild's failure must not stop the others
                logger.error("YouTube check failed for server %s: %s", server, e)

    async def _check_server(self, server: str, youtube_data: dict) -> None:
        """Notify one guild of its channels' new videos and save what it has seen.

        Each channel is handled on its own: a deleted handle or a failed API
        call is logged and retried next tick, while the others still post and
        their progress is saved.
        """
        srv_config = module_config[server]
        channel_id = srv_config.get("ChannelId")
        if not channel_id:
            return
        channel: BaseChannel = await self.bot.fetch_channel(channel_id)
        is_initial_sync = server not in youtube_data
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

        seen: dict[str, str] = dict(youtube_data.get(server) or {})
        changed = False
        for raw_handle, channel_cfg in self._iter_channels(srv_config):
            if not raw_handle:
                continue
            handle = raw_handle.lstrip("@")
            try:
                video_id = await self.get_video_id(await self.get_uploads(handle))
                if seen.get(raw_handle) == video_id:
                    continue
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
                    except (KeyError, IndexError, ValueError, AttributeError):
                        rendered = f"https://www.youtube.com/watch?v={video_id}"
                    # Shortens every link the template rendered — the video URL
                    # and any extra link the guild added to its template.
                    rendered = await shorten_text(rendered, tags=["youtube", handle])
                    await channel.send(rendered)
            except Exception as e:  # noqa: BLE001 — retried next tick, others carry on
                logger.warning("YouTube check failed for %s on server %s: %s", handle, server, e)
                continue
            # Recorded only once handled (posted or filtered out), so a failure
            # above is retried instead of silently swallowing the video.
            seen[raw_handle] = video_id
            changed = True

        if changed or is_initial_sync:
            youtube_data[server] = seen
            await YoutubeRepository(server).save_last_video_ids(seen)

    @staticmethod
    def _iter_channels(srv_config: dict):
        """Yield ``(handle, channel_cfg)`` pairs from the configured channel map."""
        for handle, cfg in (srv_config.get("youtubeChannelList") or {}).items():
            yield handle, (cfg if isinstance(cfg, dict) else {})

    @staticmethod
    async def _api(endpoint: str, **params: str | int) -> dict:
        """GET a YouTube Data API endpoint.

        The key travels in the ``X-Goog-Api-Key`` header, never in the URL, so
        it can't end up in a logged URL or an error message.
        """
        data = await fetch(
            f"{YOUTUBE_API_URL}/{endpoint}",
            return_type="json",
            params=params,
            headers={"X-Goog-Api-Key": YOUTUBE_API_KEY},
        )
        logger.debug(data)
        return data

    async def get_uploads(self, user):
        if user not in self.playlist_cache:
            data = await self._api("channels", part="contentDetails", forHandle=user)
            uploads = data["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            self.playlist_cache[user] = uploads
        else:
            uploads = self.playlist_cache[user]
        return uploads

    async def get_video_id(self, uploads):
        data = await self._api("playlistItems", part="snippet", maxResults=1, playlistId=uploads)
        return data["items"][0]["snippet"]["resourceId"]["videoId"]

    async def get_youtube_data(self):
        data = {}
        for server_id in enabled_servers:
            ids = await YoutubeRepository(server_id).get_last_video_ids()
            if ids is not None:
                data[str(server_id)] = ids
        return data

    async def is_video_valid(self, video_id, filters: dict[str, object] | None = None):
        """Decide whether to surface ``video_id`` based on per-guild filters.

        ``filters`` keys: ``shorts``, ``live``, ``vod``, ``short_max_seconds``.
        Default mirrors the legacy behaviour (VOD-only, 90 s short threshold).
        """
        if filters is None:
            filters = {"shorts": False, "live": False, "vod": True, "short_max_seconds": 90}
        data = await self._api("videos", part="snippet,contentDetails", id=video_id)
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
