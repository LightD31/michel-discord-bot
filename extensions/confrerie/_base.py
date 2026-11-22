"""What the Confrérie mixins share: the extension's clients, and its helpers.

Every mixin derives from :class:`ConfrerieBase`, so mypy checks each against
what ``ConfrerieExtension`` sets up and the methods its siblings provide. The
attributes are annotations only, and the methods are declared under
``TYPE_CHECKING``: nothing here exists at run time, so the real methods (and
the MRO) are those of the extension and its mixins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from interactions import Client

from features.confrerie.drafts import Draft
from features.confrerie.repository import DraftRepository
from src.discord_ext.messages import GuildMessages
from src.integrations.notion import NotionClient


class ConfrerieBase:
    bot: Client
    notion_client: NotionClient
    recap_messages: GuildMessages

    if TYPE_CHECKING:
        # __init__.py
        def _footer_icon(self, guild_id: str | int) -> str | None: ...

        # review.py
        def _drafts(self, guild_id: str | int) -> DraftRepository: ...
        async def _oeuvres_schema(self, settings: dict, timeout: float | None = None) -> dict: ...
        async def submit_draft(self, draft: Draft) -> bool: ...
