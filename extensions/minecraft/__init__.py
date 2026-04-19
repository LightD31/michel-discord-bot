"""Minecraft Discord extension — server status, hourly stats, scheduled events.

Assembled as a mixin composition mirroring the vlrgg/zevent packages:
- :mod:`._common` — config schema, module constants, logger
- :mod:`.status` — StatusMixin: 30s status poll, embed builders, channel rename
- :mod:`.stats` — StatsMixin: hourly stats task, table rendering, image cache
"""

from datetime import datetime

from interactions import Client, Extension, Message, listen

from features.minecraft import stats_cache
from src.discord_ext.messages import fetch_or_create_persistent_message

from ._common import (
    CHANNEL_ID_KUBZ,
    MESSAGE_ID_KUBZ,
    MINECRAFT_GUILD_ID,
    PIN_STATUS_MESSAGE,
    MinecraftConfig,
    enabled_servers,
    logger,
)
from .stats import StatsMixin
from .status import StatusMixin


class Minecraft(Extension, StatusMixin, StatsMixin):
    """Discord extension for Minecraft server monitoring and statistics."""

    def __init__(self, client):
        self.client = client
        self.image_cache = {}
        self.serverColoc = None
        self.channel_edit_timestamp = datetime.fromtimestamp(0)
        self.scheduled_event = None
        self.status_message: Message | None = None

    async def _get_status_message(self) -> Message | None:
        """Return the persistent status message, creating it on first access."""
        if self.status_message is not None:
            return self.status_message
        self.status_message = await fetch_or_create_persistent_message(
            self.bot,
            channel_id=CHANNEL_ID_KUBZ,
            message_id=MESSAGE_ID_KUBZ,
            module_name="moduleMinecraft",
            message_id_key="minecraftMessageId",
            guild_id=MINECRAFT_GUILD_ID,
            initial_content="Initialisation du statut Minecraft…",
            pin=PIN_STATUS_MESSAGE,
            logger=logger,
        )
        return self.status_message

    @listen()
    async def on_startup(self):
        """Initialize the extension on bot startup."""
        if not enabled_servers:
            logger.warning("moduleMinecraft is not enabled for any server, skipping startup")
            return

        stats_cache.clear()
        self.image_cache.clear()
        logger.info("Caches cleared on startup")

        try:
            channel = await self.bot.fetch_channel(CHANNEL_ID_KUBZ)
            guild = channel.guild
            for event in await guild.list_scheduled_events():
                creator = await event.creator
                if creator.id == self.bot.user.id and "Minecraft" in (event.name or ""):
                    self.scheduled_event = event
                    logger.info(f"Recovered existing Minecraft scheduled event: {event.name}")
                    break
        except Exception as e:
            logger.error(f"Failed to recover scheduled event: {e}")

        self.status.start()
        self.stats.start()
        await self.stats()


def setup(bot: Client) -> None:
    Minecraft(bot)


__all__ = ["Minecraft", "MinecraftConfig", "setup"]
