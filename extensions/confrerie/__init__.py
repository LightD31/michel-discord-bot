"""Extension Discord pour la Confrérie de la Plume.

Compose les mixins (stats / updates / editors / requests / review / forums /
browse) autour d'un client Notion partagé. L'API Notion utilisée est la
version ``2025-09-03`` via ``src.integrations.notion.NotionClient``; la
logique métier vit dans ``features.confrerie``.
"""

import os
import sys

# A dashboard save reloads this extension, but ``unload_extension`` only drops
# the package module itself. Without this purge the submodules below stay
# cached: the config snapshot in ``_common`` goes stale, and every command and
# task — defined on the cached mixin classes, already bound to the first
# instance — keeps running against the extension that was just unloaded.
for _name in [m for m in sys.modules if m.startswith(f"{__name__}.")]:
    del sys.modules[_name]

from interactions import Client, EmbedFooter, Extension, listen  # noqa: E402

from src.core import logging as logutil  # noqa: E402
from src.integrations.notion import NotionClient  # noqa: E402

from ._common import config, enabled_servers, guild_id, module_config  # noqa: E402
from .browse import BrowseMixin  # noqa: E402
from .editors import EditorsMixin  # noqa: E402
from .forums import ForumsMixin  # noqa: E402
from .requests import RequestsMixin  # noqa: E402
from .review import ReviewMixin  # noqa: E402
from .stats import StatsMixin  # noqa: E402
from .updates import UpdatesMixin  # noqa: E402

logger = logutil.init_logger(os.path.basename(__file__))


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

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.notion_client = NotionClient(
            auth_token=config.get("notion", {}).get("notionSecret", "")
        )
        self._recap_message = None
        self._pending_editors = {}
        self._pending_demandes = {}
        self._works_cache = None
        # A reload from the dashboard never fires Startup again: start the
        # tasks right away when the client is already running.
        if bot.is_ready:
            self._start_tasks()

    @listen()
    async def on_startup(self):
        """Warm data-source caches and start the periodic tasks."""
        self._start_tasks()
        await self._warm_data_source_cache()

    def _start_tasks(self) -> None:
        if not enabled_servers:
            logger.warning("moduleConfrerie is not enabled for any server, skipping startup")
            return
        if not config.get("notion", {}).get("notionSecret"):
            logger.warning("Secret Notion absent (config.notion.notionSecret), tâches désactivées")
            return
        for task in (self.confrerie, self.autoupdate):
            if not task.running:
                task.start()
        logger.info("Tâches de l'extension Confrérie démarrées")

    def drop(self) -> None:
        """Stop the periodic tasks before the extension goes away (reload/unload)."""
        for task in (self.confrerie, self.autoupdate):
            task.stop()
        super().drop()

    async def _warm_data_source_cache(self):
        """Pre-resolve data source ids and schemas for the configured databases.

        ``/demande`` and ``/editeur`` read the schema before opening their
        modal, inside Discord's 3 s window: a warm cache keeps that instant.
        """
        for key in (
            "confrerieNotionDbOeuvresId",
            "confrerieNotionDbIdEditorsId",
            "confrerieNotionDbOutilsId",
        ):
            db_id = module_config.get(key)
            if db_id:
                try:
                    await self.notion_client.get_properties_schema(db_id)
                except Exception as e:
                    logger.warning(f"Failed to cache data source for {db_id}: {e}")

    async def _create_embed_footer(self) -> EmbedFooter:
        """Standard footer used by stats and update embeds."""
        guild = self.bot.get_guild(guild_id) if guild_id else None
        me = guild.me if guild else None
        return EmbedFooter(
            text=me.display_name if me else "Michel",
            icon_url=guild.icon.url if guild and guild.icon else None,
        )
