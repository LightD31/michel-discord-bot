"""Suggestions Discord extension — community suggestion box with up/down voting.

Slash commands:
- ``/suggerer`` — anyone can submit a suggestion through a form (published in
  the configured channel, with a discussion thread)
- ``/suggestion approuver|refuser|implementer`` — staff decisions, ``id`` autocompleting

Buttons (👍 / 👎, and the staff's ✅ / ❌ / 🛠️) are persistent: callbacks rebind on
extension load. Per-guild config: ``moduleSuggestions`` (channel, staff role,
threads, optional anonymity).
"""

from interactions import Client, Extension, listen

from features.suggestions import SuggestionsRepository

from ._common import SUGGESTIONS, logger
from .buttons import ButtonsMixin
from .commands import CommandsMixin


class SuggestionsExtension(Extension, CommandsMixin, ButtonsMixin):
    MODULE_KEYS = ("moduleSuggestions",)

    def __init__(self, bot: Client):
        self.bot = bot

    def repository(self, guild_id: str | int) -> SuggestionsRepository:
        return SuggestionsRepository(str(guild_id))

    @listen()
    async def on_startup(self):
        for gid in SUGGESTIONS.guild_ids():
            await self.repository(gid).ensure_indexes()
        logger.info("Suggestions ready (%d guild(s))", len(SUGGESTIONS.guild_ids()))


def setup(bot: Client) -> None:
    SuggestionsExtension(bot)


__all__ = ["SuggestionsExtension", "setup"]
