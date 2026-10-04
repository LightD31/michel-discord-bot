"""Auto-update task: pushes freshly-edited Notion pages to their Discord channel."""

import os
from datetime import datetime

from interactions import Embed, EmbedFooter, OrTrigger, Task, TimeTrigger

from features.confrerie.works import Work, parse_work
from features.links import shorten_url
from src.core import logging as logutil
from src.discord_ext.embeds import Colors, format_discord_timestamp

from ._common import ConfrerieError, module_config

logger = logutil.init_logger(__name__)

# Discord caps a message's content at 2000 characters.
MESSAGE_LIMIT = 2000


def _format_date(value: str) -> str:
    try:
        return format_discord_timestamp(datetime.fromisoformat(value), "D")
    except ValueError:
        return value


async def shorten_work_links(work: Work) -> tuple[str, list[tuple[str, str]]]:
    """Short URLs for the work's Notion page and its external links."""
    notion_url = (
        await shorten_url(work.url, title=work.title, tags=["confrerie"]) if work.url else ""
    )
    links = [
        (label, await shorten_url(url, title=f"{work.title} — {label}", tags=["confrerie"]))
        for label, url in work.links
    ]
    return notion_url, links


def build_work_embed(
    work: Work,
    title: str,
    *,
    notion_url: str,
    links: list[tuple[str, str]],
    footer: EmbedFooter | None = None,
) -> Embed:
    """Embed describing one œuvre (update announcements and ``/texte``)."""
    embed = Embed(title=title, color=Colors.CONFRERIE, footer=footer, timestamp=datetime.now())
    embed.add_field(name="Titre", value=work.title[:1024], inline=True)
    if work.authors:
        embed.add_field(name="Auteur", value=", ".join(work.authors)[:1024], inline=True)
    if work.type_genre:
        embed.add_field(name="Type / Genre", value=work.type_genre[:1024], inline=False)
    if work.status:
        embed.add_field(name="Avancement", value=work.status, inline=True)
    if work.last_published:
        embed.add_field(
            name="Dernière publication", value=_format_date(work.last_published), inline=True
        )
    if notion_url:
        embed.add_field(name="Notion", value=f"[Lien vers Notion]({notion_url})", inline=True)
    for index, (label, url) in enumerate(links[:10]):
        embed.add_field(
            name="Consulter" if index == 0 else "​",
            value=f"[{label[:200]}]({url})",
            inline=True,
        )
    return embed


class UpdatesMixin:
    """Post page-update announcements and keep the "Update" flag clean on Notion."""

    async def update(self, page_id: str):
        """Fetch ``page_id`` from Notion and send its embed to the right channel."""
        page = await self.notion_client.retrieve_page(page_id)
        work = parse_work(page)

        if work.defi:
            channel_id = module_config.get("confrerieDefiChannelId")
            title = f"Nouvelle participation au {work.defi}"
        else:
            channel_id = module_config.get("confrerieNewTextChannelId")
            title = "Texte mis à jour"
        if not channel_id:
            raise ConfrerieError("Salon d'annonce non configuré pour cette page")

        channel = await self.bot.fetch_channel(channel_id)
        if not channel or not hasattr(channel, "send"):
            raise ConfrerieError(f"Canal introuvable ou invalide: {channel_id}")

        notion_url, links = await shorten_work_links(work)
        embed = build_work_embed(
            work,
            title,
            notion_url=notion_url,
            links=links,
            footer=await self._create_embed_footer(),
        )
        await channel.send(work.update_note[:MESSAGE_LIMIT] or None, embed=embed)
        logger.info(f"Message de mise à jour envoyé pour la page {page_id}")

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
