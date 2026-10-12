"""Zunivers Discord extension — /journa and !request reminders, events, corporation recap, Ninja.

Every command is a subcommand of ``/zunivers`` (``_common.zunivers_cmd``).
Assembled as a mixin composition mirroring the vlrgg/minecraft packages:
- :mod:`._common` — Pydantic config schema (``moduleZunivers``), logger, base command
- :mod:`.embeds` — Discord embed builders (events, seasons, corporation)
- :mod:`.reminders` — RemindersMixin: /zunivers journa reminders, advent calendar
- :mod:`.events` — EventsMixin: Zunivers events + hardcore season tracking, /zunivers check
- :mod:`.corporation` — CorporationMixin: daily recap + /zunivers corpo
- :mod:`.ninja` — NinjaMixin: /zunivers ninja, a player's ZUnivers Ninja plan
- :mod:`.zerator247` — Zerator247Mixin: /zunivers request, DMs when !request opens on 24/7

Migrated from the legacy ``extensions/coloc`` package. The fun commands that
used to live there are now per-server entries of
:mod:`extensions.customcommands`.
"""

from interactions import Client, Extension, GuildText, listen

from features.coloc import (
    EventState,
    ReminderCollection,
    StorageManager,
    ZuniversAPIClient,
)

from ._common import ZuniversConfig, config, enabled_servers, logger, module_config
from .corporation import CorporationMixin
from .events import EventsMixin
from .ninja import NinjaMixin
from .reminders import RemindersMixin
from .zerator247 import Zerator247Mixin


class ZuniversExtension(
    Extension, RemindersMixin, EventsMixin, CorporationMixin, NinjaMixin, Zerator247Mixin
):
    """Extension for Zunivers features: reminders, events, corporation recap, Ninja, 24/7."""

    def __init__(self, bot: Client):
        self.bot = bot
        self.api_client = ZuniversAPIClient()
        self.storage = StorageManager(
            config["misc"]["dataFolder"], guild_id=enabled_servers[0] if enabled_servers else None
        )
        self.reminders = ReminderCollection()
        self.event_state = EventState()
        self.request_subscriptions = {}
        self._last_request_cycle_end = None

    @listen()
    async def on_startup(self):
        """Initialize the extension on bot startup."""
        if not enabled_servers:
            logger.warning("moduleZunivers is not enabled for any server, skipping startup")
            return

        self.reminders = await self.storage.load_reminders()
        self.event_state = await self.storage.load_event_state()
        await self._load_request_subscriptions()

        self.daily_journa_check.start()
        self.reminder_checker.start()
        self.corporation_recap.start()
        self.events_checker.start()
        self.zerator247_checker.start()

        logger.info("Zunivers extension started successfully")

    async def async_stop(self):
        """Clean up resources when the extension stops."""
        await self.api_client.close()

    async def _get_zunivers_channel(self) -> GuildText | None:
        """Get the configured Zunivers channel."""
        try:
            channel = await self.bot.fetch_channel(module_config["colocZuniversChannelId"])
            if isinstance(channel, GuildText):
                return channel
            return None
        except Exception as e:
            logger.error(f"Could not fetch Zunivers channel: {e}")
            return None


def setup(bot: Client) -> None:
    ZuniversExtension(bot)


__all__ = ["ZuniversConfig", "ZuniversExtension", "setup"]
