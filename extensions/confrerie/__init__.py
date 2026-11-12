"""Extension Discord pour la Confrérie de la Plume.

Compose les mixins (stats / updates / editors / requests / review / forums /
browse) autour d'un client Notion partagé. L'API Notion utilisée est la
version ``2025-09-03`` via ``src.integrations.notion.NotionClient``; la
logique métier vit dans ``features.confrerie``.
"""

import os

from interactions import Client, EmbedFooter, Extension, listen

from features.confrerie.editors import EditorInput
from src.core import logging as logutil
from src.core.cache import ExpiringMap
from src.core.config import global_config
from src.discord_ext.messages import GuildMessages
from src.integrations.notion import NotionClient

from ._common import CONFRERIE, PENDING_INPUT_TTL_SECONDS
from .browse import BrowseMixin
from .editors import EditorsMixin
from .forums import ForumsMixin
from .requests import RequestsMixin
from .review import ReviewMixin
from .stats import StatsMixin
from .updates import UpdatesMixin

logger = logutil.init_logger(__name__)


class ConfrerieExtension(
    Extension,
    StatsMixin,
    UpdatesMixin,
    EditorsMixin,
    RequestsMixin,
    ReviewMixin,
    ForumsMixin,
    BrowseMixin,
):
    """Discord extension combining the confrérie stats, updates, drafts and commands."""

    MODULE_KEYS = ("moduleConfrerie",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        # One Notion integration for the bot (global section ``notion``); each
        # guild names its own databases in ``moduleConfrerie``.
        self.notion_client = NotionClient(
            auth_token=global_config().get("notion", {}).get("notionSecret", "")
        )
        self.recap_messages = GuildMessages(
            bot,
            slot="confrerie:recap",
            label="Récapitulatif Confrérie",
            channel_key="confrerieRecapChannelId",
            pin_key="confrerieRecapPinMessage",
            initial_content="Initialisation du récapitulatif…",
            logger=logger,
        )
        # What /editeur and /demande chose, until their modal is submitted.
        self._pending_editors: ExpiringMap[EditorInput] = ExpiringMap(PENDING_INPUT_TTL_SECONDS)
        self._pending_demandes: ExpiringMap[dict] = ExpiringMap(PENDING_INPUT_TTL_SECONDS)
        # Œuvres pages per Notion database, for /texte and /auteur.
        self._works_cache = {}

    @listen()
    async def on_startup(self):
        """Warm data-source caches and start the periodic tasks."""
        self._start_tasks()
        await self._warm_data_source_cache()

    def _start_tasks(self) -> None:
        if not global_config().get("notion", {}).get("notionSecret"):
            logger.warning("Secret Notion absent (config.notion.notionSecret), tâches désactivées")
            return
        for task in (self.confrerie, self.autoupdate):
            if not task.running:
                task.start()
        logger.info("Tâches de l'extension Confrérie démarrées")

    async def _warm_data_source_cache(self):
        """Pre-resolve data source ids and schemas for the configured databases.

        ``/demande`` and ``/editeur`` read the schema before opening their
        modal, inside Discord's 3 s window: a warm cache keeps that instant.
        """
        db_ids = {
            settings.get(key)
            for _, settings in CONFRERIE.items()
            for key in (
                "confrerieNotionDbOeuvresId",
                "confrerieNotionDbIdEditorsId",
                "confrerieNotionDbOutilsId",
            )
        } - {None, ""}
        for db_id in db_ids:
            try:
                await self.notion_client.get_properties_schema(db_id)
            except Exception as e:
                logger.warning(f"Failed to cache data source for {db_id}: {e}")

    async def _create_embed_footer(self, guild_id) -> EmbedFooter:
        """Standard footer used by stats and update embeds: the bot as *guild_id* sees it."""
        guild = self.bot.get_guild(guild_id) if guild_id else None
        me = guild.me if guild else None
        return EmbedFooter(
            text=me.display_name if me else "Michel",
            icon_url=guild.icon.url if guild and guild.icon else None,
        )
