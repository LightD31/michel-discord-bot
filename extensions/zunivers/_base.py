"""What the ZUnivers mixins share: the extension's client, and its state helpers.

Every mixin derives from :class:`ZuniversBase`, so mypy checks each against
what ``ZuniversExtension`` provides. The attributes are annotations only, and
the methods are declared under ``TYPE_CHECKING``: nothing here exists at run
time, so the real methods (and the MRO) are those of the extension.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from interactions import Client, GuildText

from features.coloc import ZuniversAPIClient

from ._common import GuildState


class ZuniversBase:
    bot: Client
    api_client: ZuniversAPIClient

    if TYPE_CHECKING:
        # __init__.py
        async def _state(self, guild_id: str | int) -> GuildState: ...
        async def _guilds(self) -> list[tuple[GuildState, dict]]: ...
        async def _zunivers_channel(self, guild_id: str, settings: dict) -> GuildText | None: ...
