"""Auto-update task: pushes freshly-edited Notion pages to their Discord channel."""

import os
from datetime import datetime
from typing import Any

from interactions import Embed, OrTrigger, Task, TimeTrigger

from src import logutil
from src.helpers import Colors

from ._common import ConfrerieError, module_config

logger = logutil.init_logger(os.path.basename(__file__))


class UpdatesMixin:
    """Post page-update announcements and keep the "Update" flag clean on Notion."""

    async def update(self, page_id: str):
        """Fetch ``page_id`` from Notion and send its embed to the right channel."""
        try:
            content = await self.notion_client.retrieve_page(page_id)

            channel_info = self._determine_channel_and_title(content)
            channel = await self.bot.fetch_channel(channel_info["channel_id"])

            if not channel or not hasattr(channel, "send"):
                raise ConfrerieError(f"Canal introuvable ou invalide: {channel_info['channel_id']}")

            embed = await self._create_update_embed(content, channel_info["title"])
            update_message = self._extract_update_message(content)

            await channel.send(update_message, embed=embed)
            logger.info(f"Message de mise à jour envoyé pour la page {page_id}")
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour de la page {page_id}: {e}")
            raise

    def _determine_channel_and_title(self, content: dict[str, Any]) -> dict[str, str]:
        defi_data = content["properties"].get("Défi", {}).get("select")
        if defi_data:
            return {
                "channel_id": module_config["confrerieDefiChannelId"],
                "title": f"Nouvelle participation au {defi_data['name']}",
            }
        return {
            "channel_id": module_config["confrerieNewTextChannelId"],
            "title": "Texte mis à jour",
        }

    async def _create_update_embed(self, content: dict[str, Any], title: str) -> Embed:
        footer = await self._create_embed_footer()

        embed = Embed(
            title=title,
            color=Colors.CONFRERIE,
            footer=footer,
            timestamp=datetime.now(),
        )

        titre_data = content["properties"].get("Titre", {}).get("title", [])
        if titre_data:
            embed.add_field(name="Titre", value=titre_data[0]["plain_text"], inline=True)

        auteurs_data = content["properties"].get("Auteur", {}).get("multi_select", [])
        if auteurs_data:
            embed.add_field(
                name="Auteur",
                value=", ".join(author["name"] for author in auteurs_data),
                inline=True,
            )

        self._add_genre_field(embed, content)

        embed.add_field(
            name="Notion",
            value=f"[Lien vers Notion]({content['public_url']})",
            inline=True,
        )

        self._add_consultation_links(embed, content)

        return embed

    def _add_genre_field(self, embed: Embed, content: dict[str, Any]):
        genre_texte = ""

        type_data = content["properties"].get("Type", {}).get("select")
        if type_data:
            genre_texte = type_data["name"] + " "

        genres_data = content["properties"].get("Genre", {}).get("multi_select", [])
        if genres_data:
            genre_texte += ", ".join(genre["name"] for genre in genres_data)

        if genre_texte.strip():
            embed.add_field(name="Type / Genre", value=genre_texte.strip(), inline=False)

    def _add_consultation_links(self, embed: Embed, content: dict[str, Any]):
        files_data = content["properties"].get("Lien / Fichier", {}).get("files", [])
        first_link = True

        for file in files_data:
            external_data = file.get("external")
            if external_data:
                link = f"[{file.get('name', 'Lien')}]({external_data['url']})"
                embed.add_field(
                    name="Consulter" if first_link else "\u200b",
                    value=link,
                    inline=True,
                )
                first_link = False

    def _extract_update_message(self, content: dict[str, Any]) -> str:
        update_data = content["properties"].get("Note de mise à jour", {}).get("rich_text", [])
        return update_data[0]["plain_text"] if update_data else ""

    @Task.create(
        OrTrigger(
            TimeTrigger(hour=0, utc=False),
            TimeTrigger(hour=8, utc=False),
            TimeTrigger(hour=10, utc=False),
            TimeTrigger(hour=14, utc=False),
            TimeTrigger(hour=18, utc=False),
            TimeTrigger(hour=20, utc=False),
            TimeTrigger(hour=22, utc=False),
        )
    )
    async def autoupdate(self):
        """Scheduled sweep of pages marked with ``Update=True`` in Notion."""
        logger.debug("Début de la tâche de mise à jour automatique")

        try:
            updated_pages = await self.notion_client.query_data_source(
                database_id=module_config["confrerieNotionDbOeuvresId"],
                filter_params={"property": "Update", "checkbox": {"equals": True}},
            )

            if not updated_pages:
                logger.debug("Aucune page à mettre à jour")
                return

            logger.info(f"Traitement de {len(updated_pages)} page(s) à mettre à jour")

            for page in updated_pages:
                try:
                    await self.update(page["id"])
                    await self.notion_client.update_page_properties(
                        page_id=page["id"], properties={"Update": {"checkbox": False}}
                    )
                    logger.debug(f"Page {page['id']} mise à jour avec succès")
                except Exception as e:
                    logger.error(f"Erreur lors de la mise à jour de la page {page['id']}: {e}")
        except Exception as e:
            logger.error(f"Erreur lors de la tâche d'auto-update: {e}")
