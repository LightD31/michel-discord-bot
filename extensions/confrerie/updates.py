"""Auto-update task: pushes freshly-edited Notion pages to their Discord channel."""

import os
from datetime import datetime

from interactions import Embed, OrTrigger, Task, TimeTrigger

from features.confrerie.works import Work, parse_work
from features.links import shorten_url
from src.core import clock, log_context
from src.core import logging as logutil
from src.core.logging import log_failure
from src.core.text import format_discord_timestamp
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import card

from ._base import ConfrerieBase
from ._common import CONFRERIE, ConfrerieError, notion_secret

logger = logutil.init_logger(__name__)

# Discord caps a message's content at 2000 characters.
MESSAGE_LIMIT = 2000


def _format_date(value: str) -> str:
    try:
        return format_discord_timestamp(datetime.fromisoformat(value), "D")
    except ValueError:
        return value


async def shorten_work_links(guild_id: str, work: Work) -> tuple[str, list[tuple[str, str]]]:
    """Short URLs for the work's Notion page and its external links, on the server's Shlink."""
    notion_url = (
        await shorten_url(work.url, guild_id=guild_id, title=work.title, tags=["confrerie"])
        if work.url
        else ""
    )
    links = [
        (
            label,
            await shorten_url(
                url, guild_id=guild_id, title=f"{work.title} — {label}", tags=["confrerie"]
            ),
        )
        for label, url in work.links
    ]
    return notion_url, links


def build_work_embed(
    work: Work,
    title: str,
    *,
    notion_url: str,
    links: list[tuple[str, str]],
    footer_icon: str | None = None,
) -> Embed:
    """Embed describing one œuvre (update announcements and ``/texte``)."""
    embed = card("moduleConfrerie", title=title, footer_icon=footer_icon, timestamp=clock.now())
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


class UpdatesMixin(ConfrerieBase):
    """Post page-update announcements and keep the "Update" flag clean on Notion."""

    async def update(self, page_id: str, guild_id: str, settings: dict):
        """Fetch ``page_id`` from Notion and send its embed to the guild's right channel."""
        page = await self.notion(settings).retrieve_page(page_id)
        work = parse_work(page)

        if work.defi:
            channel_id = settings.get("confrerieDefiChannelId")
            title = f"Nouvelle participation au {work.defi}"
        else:
            channel_id = settings.get("confrerieNewTextChannelId")
            title = "Texte mis à jour"
        if not channel_id:
            raise ConfrerieError("Salon d'annonce non configuré pour cette page")

        channel = await fetch_guild_channel(self.bot, guild_id, channel_id, logger=logger)
        if not channel or not hasattr(channel, "send"):
            raise ConfrerieError(f"Canal introuvable ou invalide: {channel_id}")

        notion_url, links = await shorten_work_links(guild_id, work)
        embed = build_work_embed(
            work,
            title,
            notion_url=notion_url,
            links=links,
            footer_icon=self._footer_icon(guild_id),
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
        """Scheduled sweep of pages marked with ``Update=True``, in each enabled guild's base."""
        logger.debug("Début de la tâche de mise à jour automatique")
        for guild_id, settings in CONFRERIE.items():
            with log_context.scope(guild_id=guild_id):
                db_id = settings.get("confrerieNotionDbOeuvresId")
                if db_id and notion_secret(settings):  # its own Notion token (P37)
                    await self._autoupdate_guild(guild_id, settings, db_id)

    async def _autoupdate_guild(self, guild_id: str, settings: dict, db_id: str) -> None:
        try:
            updated_pages = await self.notion(settings).query_data_source(
                database_id=db_id,
                filter_params={"property": "Update", "checkbox": {"equals": True}},
            )

            if not updated_pages:
                logger.debug("Aucune page à mettre à jour")
                return

            logger.info(f"Traitement de {len(updated_pages)} page(s) à mettre à jour")

            for page in updated_pages:
                try:
                    await self.update(page["id"], guild_id, settings)
                    await self.notion(settings).update_page_properties(
                        page_id=page["id"], properties={"Update": {"checkbox": False}}
                    )
                    logger.debug(f"Page {page['id']} mise à jour avec succès")
                except Exception as e:
                    log_failure(
                        logger, f"Erreur lors de la mise à jour de la page {page['id']}: {e}"
                    )
        except Exception as e:
            log_failure(logger, f"Erreur lors de la tâche d'auto-update: {e}")
