"""Extension MDI Tracker — suit le tournoi Mythic Dungeon International (Raider.IO).

Configuration par serveur via le dashboard Web (``moduleMDI``):
    - notificationChannelId: salon des annonces
    - scheduleChannelMessageId: id du message de planning (channelId:messageId)
    - eventSlug: slug Raider.IO de l'événement
    - teamSlug: slug Raider.IO de l'équipe à suivre (par défaut ``mandatory``)
    - pinSchedule: épingler le planning
    - pingRoleId: rôle à mentionner quand un match passe en direct

The extension is assembled as a mixin composition: data fetching lives in
:class:`.api.ApiMixin`, embed building in :class:`.embeds.EmbedsMixin`, task
scheduling and Discord posting in :class:`.notifications.NotificationsMixin`.
"""

from __future__ import annotations

from interactions import Client, Extension, listen

from ._common import (
    GuildConfig,
    GuildState,
    enabled_servers,
    logger,
    module_config,
)
from .api import ApiMixin
from .embeds import EmbedsMixin
from .notifications import NotificationsMixin


class MdiExtension(ApiMixin, EmbedsMixin, NotificationsMixin, Extension):
    """Discord extension combining Raider.IO fetch, embeds, and notifications."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._servers: dict[str, GuildState] = {}
        self._last_inactive_run: dict[str, float] = {}

    @listen()
    async def on_startup(self) -> None:
        """Initialise per-guild state and start the scheduled tasks."""
        try:
            await self._initialize_all_servers()
        except Exception as e:
            logger.error("MDI: initialisation failed: %s", e)
            return
        try:
            self.schedule.start()
            self.live_update.start()
        except Exception as e:
            logger.error("MDI: failed to start tasks: %s", e)

    async def _initialize_all_servers(self) -> None:
        for server_id in enabled_servers:
            srv_cfg_raw = module_config.get(server_id, {}) or {}
            gc = GuildConfig.from_dict(srv_cfg_raw)
            if not gc.notification_channel_id:
                logger.warning("MDI: guild %s has no notificationChannelId, skipping", server_id)
                continue

            state = GuildState(server_id=server_id, guild_config=gc)

            # Load the notification channel
            try:
                state.notification_channel = await self.bot.fetch_channel(
                    gc.notification_channel_id
                )
            except Exception as e:
                logger.warning(
                    "MDI: guild %s — could not load notification channel %s: %s",
                    server_id,
                    gc.notification_channel_id,
                    e,
                )

            # Try to recover the schedule message if configured
            if gc.schedule_channel_id and gc.schedule_message_id:
                state.schedule_channel = state.notification_channel
                if (
                    state.schedule_channel is None
                    or str(getattr(state.schedule_channel, "id", "")) != gc.schedule_channel_id
                ):
                    try:
                        state.schedule_channel = await self.bot.fetch_channel(
                            gc.schedule_channel_id
                        )
                    except Exception as e:
                        logger.warning(
                            "MDI: guild %s — schedule channel %s unreachable: %s",
                            server_id,
                            gc.schedule_channel_id,
                            e,
                        )
                if state.schedule_channel is not None and hasattr(
                    state.schedule_channel, "fetch_message"
                ):
                    try:
                        state.schedule_message = await state.schedule_channel.fetch_message(
                            gc.schedule_message_id
                        )
                    except Exception as e:
                        logger.info(
                            "MDI: guild %s — stored schedule message %s not found, will recreate"
                            " (%s)",
                            server_id,
                            gc.schedule_message_id,
                            e,
                        )

            await self._load_persisted_matches(state)
            self._servers[server_id] = state
            logger.info(
                "MDI: guild %s initialised (team_slug=%s, %d known match(es))",
                server_id,
                gc.team_slug,
                len(state.matches),
            )


def setup(bot: Client) -> None:
    MdiExtension(bot)
