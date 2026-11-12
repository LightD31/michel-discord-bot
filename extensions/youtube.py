"""Extension YouTube — notifications automatiques de nouvelles vidéos."""

from interactions import BaseChannel, Client, Extension, IntervalTrigger, Task, listen
from pydantic import field_validator

from features.links import shorten_text
from features.youtube import (
    DEFAULT_TEMPLATE,
    YoutubeRepository,
    classify,
    render_notification,
    short_max_seconds,
    wanted,
)
from src.core import logging as logutil
from src.core.config import ModuleConfig, global_config
from src.core.http import fetch
from src.core.logging import log_failure
from src.discord_ext.mentions import everyone_if_written
from src.webui.schemas import SchemaBase, check_template, enabled_field, register_module, ui


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

    @field_validator("youtubeNotificationTemplate")
    @classmethod
    def _plain_placeholders(cls, template: str) -> str:
        return check_template(template)


logger = logutil.init_logger(__name__)
YOUTUBE = ModuleConfig("moduleYoutube")


def _setting(name: str) -> str:
    """A field of the global section ``youtube``, read at each call."""
    return str((global_config().get("youtube") or {}).get(name) or "").strip()


def _api_key() -> str:
    """The YouTube Data API key."""
    return _setting("youtubeApiKey")


def _api_url() -> str:
    """The YouTube Data API base URL, without a trailing slash."""
    return _setting("youtubeApiUrl").rstrip("/")


class YoutubeExtension(Extension):
    MODULE_KEYS = ("moduleYoutube",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.playlist_cache = {}  # Add a cache for playlists

    @listen()
    async def on_startup(self):
        self.check_youtube.start()
        # await self.check_youtube()

    @Task.create(IntervalTrigger(minutes=5))
    async def check_youtube(self):
        if not _api_key():
            logger.warning("No YouTube API key (youtube.youtubeApiKey): skipping the check")
            return
        if not _api_url():
            logger.warning("No YouTube API URL (youtube.youtubeApiUrl): skipping the check")
            return
        # One read of every guild's last-seen videos per tick, not one per guild.
        youtube_data = await self.get_youtube_data()
        for server in YOUTUBE.guild_ids():
            try:
                await self._check_server(str(server), youtube_data)
            except Exception as e:  # noqa: BLE001 — one guild's failure must not stop the others
                log_failure(logger, "YouTube check failed for server %s: %s", server, e)

    async def _check_server(self, server: str, youtube_data: dict) -> None:
        """Notify one guild of its channels' new videos and save what it has seen.

        Each channel is handled on its own: a deleted handle or a failed API
        call is logged and retried next tick, while the others still post and
        their progress is saved.
        """
        srv_config = YOUTUBE.get(server)
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
        template = srv_config.get("youtubeNotificationTemplate") or DEFAULT_TEMPLATE
        short_max = short_max_seconds(srv_config)

        channels = [(handle, cfg) for handle, cfg in self._iter_channels(srv_config) if handle]
        followed = {handle for handle, _ in channels}
        stored: dict[str, str] = youtube_data.get(server) or {}
        # Forget channels the guild no longer follows.
        seen = {handle: video for handle, video in stored.items() if handle in followed}
        changed = seen != stored
        for raw_handle, channel_cfg in channels:
            handle = raw_handle.lstrip("@")
            try:
                video_id = await self.get_video_id(await self.get_uploads(handle))
                if seen.get(raw_handle) == video_id:
                    continue
                if not is_initial_sync and await self.is_video_valid(
                    video_id, channel_cfg, short_max
                ):
                    rendered = render_notification(
                        template,
                        video_id=video_id,
                        handle=handle,
                        label=channel_cfg.get("label") or handle,
                    )
                    # Shortens every link the template rendered — the video URL
                    # and any extra link the guild added to its template.
                    rendered = await shorten_text(rendered, tags=["youtube", handle])
                    # @everyone pings only when the template writes it, not
                    # through a value (a handle) it is filled with.
                    await channel.send(rendered, allowed_mentions=everyone_if_written(template))
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
            f"{_api_url()}/{endpoint}",
            return_type="json",
            params=params,
            headers={"X-Goog-Api-Key": _api_key()},
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
        for server_id in YOUTUBE.guild_ids():
            ids = await YoutubeRepository(server_id).get_last_video_ids()
            if ids is not None:
                data[str(server_id)] = ids
        return data

    async def is_video_valid(self, video_id: str, channel_cfg: dict, short_max: int) -> bool:
        """Whether the channel's Shorts / Lives / VOD toggles ask for this video."""
        data = await self._api("videos", part="snippet,contentDetails", id=video_id)
        kind = classify(data["items"][0], short_max)
        if wanted(kind, channel_cfg):
            return True
        logger.info("New video %s is a %s — skipped by the channel's filters", video_id, kind)
        return False
