"""Suggestions Discord extension — community suggestion box with up/down voting.

Slash commands:
- ``/suggest`` — anyone can submit a suggestion (published in the configured channel)
- ``/suggestion approve|deny|implement`` — staff-only status updates

Buttons (👍 / 👎) are persistent: callbacks rebind on extension load. Per-guild
config: ``moduleSuggestions`` (channel, staff role, optional anonymity).
"""

from interactions import Client, Extension, listen

from features.suggestions import SuggestionsRepository

from ._common import enabled_servers, logger
from .buttons import ButtonsMixin
from .commands import CommandsMixin


class SuggestionsExtension(Extension, CommandsMixin, ButtonsMixin):
    def __init__(self, bot: Client):
        self.bot = bot
        self._repos: dict[str, SuggestionsRepository] = {}

    def repository(self, guild_id: str | int) -> SuggestionsRepository:
        gid = str(guild_id)
        repo = self._repos.get(gid)
        if repo is None:
            repo = SuggestionsRepository(gid)
            self._repos[gid] = repo
        return repo

    @listen()
    async def on_startup(self):
        for gid in enabled_servers:
            await self.repository(gid).ensure_indexes()
        logger.info("Suggestions ready (%d guild(s))", len(enabled_servers))


def setup(bot: Client) -> None:
    SuggestionsExtension(bot)


__all__ = ["SuggestionsExtension", "setup"]
