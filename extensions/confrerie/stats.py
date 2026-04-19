"""Periodic statistics task: counts per author / per défi, posted to a recap message."""

import os
from collections import defaultdict
from datetime import datetime
from typing import Any

from interactions import Embed, Task, TimeTrigger

from src import logutil
from src.helpers import Colors, fetch_or_create_persistent_message

from ._common import ConfrerieError, enabled_servers, module_config

logger = logutil.init_logger(os.path.basename(__file__))


class StatsMixin:
    """Refresh and post the confrérie statistics recap embed."""

    @Task.create(TimeTrigger(utc=False))
    async def confrerie(self):
        """Hourly stats refresh + recap message edit."""
        logger.debug("Début de la tâche de statistiques de la confrérie")
        try:
            if self._is_cache_valid():
                logger.debug("Utilisation du cache pour les statistiques")
                stats_data = self._stats_cache
            else:
                stats_data = await self._fetch_statistics()

            await self._update_statistics_message(stats_data)
            logger.debug("Statistiques de la confrérie mises à jour avec succès")
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour des statistiques: {e}")

    def _is_cache_valid(self) -> bool:
        if not self._cache_timestamp:
            return False
        return (datetime.now() - self._cache_timestamp).total_seconds() < self._cache_duration

    async def _fetch_statistics(self) -> dict[str, Any]:
        """Query Notion and materialise the sorted author/défi counts."""
        logger.debug("Récupération des statistiques depuis Notion")

        results = await self.notion_client.query_data_source(
            database_id=module_config["confrerieNotionDbOeuvresId"],
            filter_params={"property": "Défi", "select": {"is_not_empty": True}},
        )

        authors: dict[str, int] = defaultdict(int)
        defis: dict[str, int] = defaultdict(int)

        for result in results:
            for author in result["properties"]["Auteur"]["multi_select"]:
                authors[author["name"]] += 1

            defi_data = result["properties"]["Défi"]["select"]
            if defi_data:
                defis[defi_data["name"]] += 1

        sorted_authors = sorted(authors.items(), key=lambda x: x[1], reverse=True)
        sorted_defis = sorted(defis.items(), key=lambda x: x[1], reverse=True)

        stats_data = {
            "authors": sorted_authors,
            "defis": sorted_defis,
            "timestamp": datetime.now(),
        }

        self._stats_cache = stats_data
        self._cache_timestamp = datetime.now()

        return stats_data

    async def _update_statistics_message(self, stats_data: dict[str, Any]):
        try:
            if self._recap_message is None:
                guild_id = enabled_servers[0] if enabled_servers else None
                self._recap_message = await fetch_or_create_persistent_message(
                    self.bot,
                    channel_id=module_config.get("confrerieRecapChannelId"),
                    message_id=module_config.get("confrerieRecapMessageId"),
                    module_name="moduleConfrerie",
                    message_id_key="confrerieRecapMessageId",
                    guild_id=guild_id,
                    initial_content="Initialisation du récapitulatif…",
                    pin=bool(module_config.get("confrerieRecapPinMessage", False)),
                    logger=logger,
                )
                if self._recap_message is None:
                    raise ConfrerieError("Canal de récapitulatif introuvable ou invalide")

            embed = await self._create_statistics_embed(stats_data)
            footer = await self._create_embed_footer()
            embed.set_footer(text=footer.text, icon_url=footer.icon_url)

            await self._recap_message.edit(
                content=(
                    "Retrouvez tous les textes en [cliquant ici]"
                    "(https://drndvs.link/Confrerie 'Notion de la confrérie')"
                ),
                embed=embed,
            )
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour du message: {e}")
            raise

    async def _create_statistics_embed(self, stats_data: dict[str, Any]) -> Embed:
        embed = Embed(
            title="Statistiques de la confrérie",
            color=Colors.CONFRERIE,
            timestamp=stats_data["timestamp"],
        )

        authors_text = "\n".join(
            f"{author} : **{count}** défi{'s' if count > 1 else ''}"
            for author, count in stats_data["authors"][:10]
        )
        defis_text = "\n".join(
            f"{defi} : **{count}** texte{'s' if count > 1 else ''}"
            for defi, count in stats_data["defis"][:10]
        )

        embed.add_field(
            name="Auteurs les plus prolifiques",
            value=authors_text or "Aucun auteur trouvé",
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(
            name="Défis les plus populaires",
            value=defis_text or "Aucun défi trouvé",
            inline=True,
        )

        return embed
