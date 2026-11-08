"""Zunivers Discord extension — /journa and !request reminders, events, corporation recap, Ninja.

Every command is a subcommand of ``/zunivers`` (``_common.zunivers_cmd``).
Assembled as a mixin composition mirroring the vlrgg/minecraft packages:
- :mod:`._common` — Pydantic config schema (``moduleZunivers``), logger, base command
- :mod:`.embeds` — Discord embed builders (events, seasons, corporation)
- :mod:`.reminders` — RemindersMixin: /zunivers journa reminders, advent calendar
- :mod:`.events` — EventsMixin: Zunivers events + hardcore season tracking, /zunivers check
- :mod:`.corporation` — CorporationMixin: daily recap + /zunivers corpo
- :mod:`.ninja` — NinjaMixin: /zunivers ninja, a player's ZUnivers Ninja plan
- :mod:`.zerator247` — Zerator247Mixin: /zunivers 247, DMs when !request opens on 24/7

Migrated from the legacy ``extensions/coloc`` package. The fun commands that
used to live there are now per-server entries of
:mod:`extensions.customcommands`.
"""

import asyncio

from interactions import Client, Extension, GuildText, listen

from features.coloc import StorageManager, ZuniversAPIClient

from ._common import ZUNIVERS, GuildState, ZuniversConfig, logger, zunivers_api_url
from .corporation import CorporationMixin
from .events import EventsMixin
from .ninja import NinjaMixin
from .reminders import RemindersMixin
from .zerator247 import Zerator247Mixin


class ZuniversExtension(
    Extension, RemindersMixin, EventsMixin, CorporationMixin, NinjaMixin, Zerator247Mixin
):
    """Extension for Zunivers features: reminders, events, corporation recap, Ninja, 24/7."""

    MODULE_KEYS = ("moduleZunivers",)

    def __init__(self, bot: Client):
        self.bot = bot
        self.api_client = ZuniversAPIClient(zunivers_api_url)
        # Reminders, event state and !request subscriptions, per guild.
        self._states: dict[str, GuildState] = {}
        self._states_lock = asyncio.Lock()

    @listen()
    async def on_startup(self):
        """Start the tasks; each reads the enabled guilds and their settings when it runs."""
        for task in (
            self.daily_journa_check,
            self.reminder_checker,
            self.corporation_recap,
            self.events_checker,
            self.zerator247_checker,
        ):
            if not task.running:
                task.start()
        logger.info("Zunivers extension started successfully")

    async def async_stop(self):
        """Clean up resources when the extension stops."""
        await self.api_client.close()

    async def _state(self, guild_id) -> GuildState:
        """The guild's state, loaded from its database the first time it is needed."""
        gid = str(guild_id)
        async with self._states_lock:
            state = self._states.get(gid)
            if state is None:
                storage = StorageManager(gid)
                state = GuildState(
                    guild_id=gid,
                    storage=storage,
                    reminders=await storage.load_reminders(),
                    event_state=await storage.load_event_state(),
                    request_subscriptions=await self._load_request_subscriptions(gid),
                )
                self._states[gid] = state
            return state

    async def _guilds(self) -> list[tuple[GuildState, dict]]:
        """``(state, settings)`` for each guild where the module is on, now."""
        guilds = ZUNIVERS.items()
        enabled = {guild_id for guild_id, _ in guilds}
        for guild_id in [g for g in self._states if g not in enabled]:
            del self._states[guild_id]
        return [(await self._state(guild_id), settings) for guild_id, settings in guilds]

    async def _zunivers_channel(self, settings: dict) -> GuildText | None:
        """The guild's configured Zunivers channel."""
        channel_id = settings.get("colocZuniversChannelId")
        if not channel_id:
            return None
        try:
            channel = await self.bot.fetch_channel(channel_id)
            if isinstance(channel, GuildText):
                return channel
            return None
        except Exception as e:
            logger.error(f"Could not fetch Zunivers channel: {e}")
            return None


def setup(bot: Client) -> None:
    ZuniversExtension(bot)


__all__ = ["ZuniversConfig", "ZuniversExtension", "setup"]
