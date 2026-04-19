"""Coloc Discord extension — Zunivers integration + shared coloc commands.

Assembled as a mixin composition mirroring the vlrgg/zevent/minecraft packages:
- :mod:`._common` — Pydantic config schema (``moduleColoc``), logger, module cfg
- :mod:`.reminders` — RemindersMixin: /journa reminders, advent calendar
- :mod:`.events` — EventsMixin: Zunivers events + hardcore season tracking
- :mod:`.corporation` — CorporationMixin: daily corporation recap, /corpo

Non-Zunivers fun commands (/fesse, /massageducul) live on the top-level
extension class below.
"""

from interactions import Client, Extension, SlashContext, listen, slash_command

from features.coloc import (
    EventState,
    ReminderCollection,
    StorageManager,
    ZuniversAPIClient,
)

from ._common import ColocConfig, config, enabled_servers, logger, module_config
from .corporation import CorporationMixin
from .events import EventsMixin
from .reminders import RemindersMixin


class ColocExtension(Extension, RemindersMixin, EventsMixin, CorporationMixin):
    """Extension for Zunivers-related features and shared coloc commands."""

    def __init__(self, bot: Client):
        self.bot = bot
        self.api_client = ZuniversAPIClient()
        self.storage = StorageManager(
            config["misc"]["dataFolder"], guild_id=enabled_servers[0] if enabled_servers else None
        )
        self.reminders = ReminderCollection()
        self.event_state = EventState()

    @listen()
    async def on_startup(self):
        """Initialize the extension on bot startup."""
        if not enabled_servers:
            logger.warning("moduleColoc is not enabled for any server, skipping startup")
            return

        self.reminders = await self.storage.load_reminders()
        self.event_state = await self.storage.load_event_state()

        self.daily_journa_check.start()
        self.reminder_checker.start()
        self.corporation_recap.start()
        self.events_checker.start()

        logger.info("Coloc extension started successfully")

    async def async_stop(self):
        """Clean up resources when the extension stops."""
        await self.api_client.close()

    # ==================== Fun Commands ====================

    @slash_command(name="fesse", description="Fesses", scopes=enabled_servers)
    async def fesse(self, ctx: SlashContext):
        await ctx.send(
            "https://media1.tenor.com/m/YIUbUoKi8ZcAAAAC/sesame-street-kermit-the-frog.gif"
        )

    @slash_command(
        name="massageducul",
        description="Massage du cul",
        scopes=enabled_servers,
    )
    async def massageducul(self, ctx: SlashContext):
        await ctx.send("https://media1.tenor.com/m/h6OvENNtJh0AAAAC/bebou.gif")

    async def _get_zunivers_channel(self):
        """Get the configured Zunivers channel."""
        from interactions import GuildText

        try:
            channel = await self.bot.fetch_channel(module_config["colocZuniversChannelId"])
            if isinstance(channel, GuildText):
                return channel
            return None
        except Exception as e:
            logger.error(f"Could not fetch Zunivers channel: {e}")
            return None


def setup(bot: Client) -> None:
    ColocExtension(bot)


__all__ = ["ColocConfig", "ColocExtension", "setup"]
