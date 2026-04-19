"""Extension Discord pour la gestion de la confrérie littéraire.

Compose les mixins (stats / updates / editors / requests) autour d'un client
Notion partagé. L'API Notion utilisée est la version ``2025-09-03`` via
``src.integrations.notion.NotionClient``.
"""

import os
from datetime import datetime
from typing import Any

from interactions import Client, EmbedFooter, Extension, listen

from src.core import logging as logutil
from src.integrations.notion import NotionClient

from ._common import config, enabled_servers, module_config
from .editors import EditorsMixin
from .requests import RequestsMixin
from .stats import StatsMixin
from .updates import UpdatesMixin

logger = logutil.init_logger(os.path.basename(__file__))


class ConfrerieExtension(Extension, StatsMixin, UpdatesMixin, EditorsMixin, RequestsMixin):
    """Discord extension combining the confrérie stats, updates, and slash commands."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.data: dict[str, Any] = {}
        self.notion_client = NotionClient(auth_token=config["notion"]["notionSecret"])
        self._stats_cache: dict[str, Any] = {}
        self._cache_timestamp: datetime | None = None
        self._cache_duration = 300
        self._recap_message = None

    @listen()
    async def on_startup(self):
        """Warm data-source caches and start the periodic tasks."""
        if not enabled_servers:
            logger.warning("moduleConfrerie is not enabled for any server, skipping startup")
            return
        logger.info("Démarrage de l'extension Confrérie")
        try:
            await self._warm_data_source_cache()
            self.confrerie.start()
            self.autoupdate.start()
            logger.info("Tâches de l'extension Confrérie démarrées avec succès")
        except Exception as e:
            logger.error(f"Erreur lors du démarrage des tâches: {e}")

    async def _warm_data_source_cache(self):
        """Pre-resolve Notion data source ids for the configured databases."""
        for db_id in (
            module_config.get("confrerieNotionDbOeuvresId"),
            module_config.get("confrerieNotionDbIdEditorsId"),
        ):
            if db_id:
                try:
                    await self.notion_client.get_data_source_id(db_id)
                except Exception as e:
                    logger.warning(f"Failed to cache data source for {db_id}: {e}")

    async def _create_embed_footer(self) -> EmbedFooter:
        """Standard footer used by stats and update embeds."""
        try:
            bot = await self.bot.fetch_member(self.bot.user.id, enabled_servers[0])
            guild = await self.bot.fetch_guild(enabled_servers[0])
            return EmbedFooter(
                text=bot.display_name if bot else "Michel",
                icon_url=guild.icon.url if guild and guild.icon else None,
            )
        except Exception as e:
            logger.warning(f"Impossible de créer le footer: {e}")
            return EmbedFooter(text="Michel")
