"""Minecraft Discord extension — server status, hourly stats, scheduled events.

Assembled as a mixin composition mirroring the vlrgg/zevent packages:
- :mod:`._common` — config schema, live config accessor, logger
- :mod:`.status` — StatusMixin: 30s status poll, embed builders, channel rename
- :mod:`.stats` — StatsMixin: hourly stats task, table rendering, image cache
"""

from datetime import datetime

from interactions import Client, Extension, listen

from features.minecraft import clear_stats_caches
from src.discord_ext.messages import GuildMessages

from ._common import MINECRAFT, MinecraftConfig, logger
from .stats import StatsMixin
from .status import StatusMixin


class Minecraft(Extension, StatusMixin, StatsMixin):
    """Each enabled guild's Minecraft server: status every 30 s, player stats hourly.

    The settings are read at each refresh, so a dashboard save (another
    server, channel or SFTP account) applies at the next one without a reload.
    """

    MODULE_KEYS = ("moduleMinecraft",)

    def __init__(self, client):
        self.client = client
        self.image_cache = {}
        self.status_messages = GuildMessages(
            client,
            module_name=MINECRAFT.key,
            channel_key="minecraftChannelId",
            message_key="minecraftMessageId",
            pin_key="minecraftPinMessage",
            initial_content="Initialisation du statut Minecraft…",
            logger=logger,
        )
        # Per guild: the last stats table shown, the last channel rename, and
        # the scheduled event announcing players online (looked up once).
        self._last_stats_tables: dict[str, str | None] = {}
        self.channel_edit_timestamps: dict[str, datetime] = {}
        self.scheduled_events: dict[str, object] = {}
        self._events_recovered: set[str] = set()

    def _forget_disabled_guilds(self, enabled: list[str]) -> None:
        self.status_messages.keep_only(enabled)
        for state in (self._last_stats_tables, self.channel_edit_timestamps, self.scheduled_events):
            for guild_id in [g for g in state if g not in enabled]:
                del state[guild_id]
        self._events_recovered &= set(enabled)

    @listen()
    async def on_startup(self):
        """Start the status and stats tasks; each reads the enabled guilds when it runs."""
        clear_stats_caches()
        self.image_cache.clear()
        logger.info("Caches cleared on startup")
        if not self.status.running:
            self.status.start()
        if not self.stats.running:
            self.stats.start()
        await self.stats()


def setup(bot: Client) -> None:
    Minecraft(bot)


__all__ = ["Minecraft", "MinecraftConfig", "setup"]
