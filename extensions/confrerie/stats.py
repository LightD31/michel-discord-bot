"""Periodic statistics task: the confrérie recap message, refreshed hourly."""

import os
from datetime import datetime

from interactions import Embed, OrTrigger, Task, TimeTrigger

from features.confrerie import notion_props as np
from features.confrerie.stats import COL_DEFI, ConfrerieStats, aggregate
from features.links import shorten_keyed
from src.core import clock
from src.core import logging as logutil
from src.core.logging import log_failure
from src.core.text import take_within_budget
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import edit_message_if_changed

from ._common import CONFRERIE, ConfrerieError

logger = logutil.init_logger(__name__)

FIELD_BUDGET = 1024
TOP_ROWS = 10


def plural(count: int, word: str, plural_form: str | None = None) -> str:
    # French keeps 0 and 1 in the singular ("0 participation").
    return f"{count} {word if count <= 1 else plural_form or word + 's'}"


def _lines(entries: list[str], empty: str) -> str:
    kept, _ = take_within_budget(entries[:TOP_ROWS], FIELD_BUDGET, separator="\n")
    return "\n".join(kept) or empty


def build_statistics_embed(stats: ConfrerieStats) -> Embed:
    """Render the recap embed. Pure given ``stats`` so unchanged data re-renders identically."""
    embed = Embed(
        title="Statistiques de la confrérie",
        description=(
            f"**{plural(stats.total_works, 'œuvre')}** · "
            f"**{plural(stats.total_defi_entries, 'participation')}** aux défis · "
            f"**{plural(stats.author_count, 'auteur·ice')}**"
        ),
        color=Colors.CONFRERIE,
        timestamp=clock.now(),
    )

    embed.add_field(
        name="✍️ Auteur·ices les plus prolifiques",
        value=_lines(
            [
                f"{a.name} : **{plural(a.works, 'œuvre')}** · {plural(a.defis, 'défi')}"
                for a in stats.authors
            ],
            "Aucun auteur trouvé",
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 Défis les plus suivis",
        value=_lines(
            [f"{name} : **{plural(n, 'texte')}**" for name, n in stats.numbered_defis],
            "Aucun défi trouvé",
        ),
        inline=True,
    )
    if stats.series:
        embed.add_field(
            name="📚 Séries",
            value=_lines(
                [f"{name} : **{plural(n, 'texte')}**" for name, n in stats.series],
                "—",
            ),
            inline=True,
        )
    if stats.latest_defi:
        name, count = stats.latest_defi
        embed.add_field(
            name="🆕 Dernier défi",
            value=f"{name} — {plural(count, 'participation')}",
            inline=False,
        )
    if stats.in_progress:
        embed.add_field(
            name="🛠️ En chantier",
            value=_lines(
                [
                    f"*{w.title}* ({w.status})"
                    + (f" — {', '.join(w.authors)}" if w.authors else "")
                    for w in stats.in_progress
                ],
                "—",
            ),
            inline=False,
        )
    return embed


class StatsMixin:
    """Refresh and post the confrérie statistics recap embed."""

    @Task.create(OrTrigger(*[TimeTrigger(hour=h, utc=False) for h in range(24)]))
    async def confrerie(self):
        """Hourly stats refresh + recap message edit, in each enabled guild."""
        logger.debug("Début de la tâche de statistiques de la confrérie")
        guilds = CONFRERIE.items()
        self.recap_messages.keep_only([guild_id for guild_id, _ in guilds])
        # Guilds sharing a Notion database share one aggregation per run.
        stats_by_db: dict[str, ConfrerieStats] = {}
        for guild_id, settings in guilds:
            db_id = settings.get("confrerieNotionDbOeuvresId")
            if not db_id:
                continue
            try:
                if db_id not in stats_by_db:
                    stats_by_db[db_id] = await self._fetch_statistics(db_id)
                await self._update_statistics_message(guild_id, settings, stats_by_db[db_id])
                logger.debug("Statistiques de la confrérie mises à jour (serveur %s)", guild_id)
            except Exception as e:
                log_failure(logger, f"Erreur lors de la mise à jour des statistiques: {e}")

    async def _fetch_statistics(self, db_id: str) -> ConfrerieStats:
        """Query every œuvre (all pages) of *db_id* and aggregate them."""
        pages = await self.notion_client.query_data_source(database_id=db_id)
        try:
            schema = await self.notion_client.get_properties_schema(db_id)
            defi_options = np.schema_options(schema, COL_DEFI)
        except Exception as e:  # noqa: BLE001 — the options only refine "dernier défi"
            logger.warning("Schéma Notion des œuvres indisponible: %s", e)
            defi_options = []
        return aggregate(pages, defi_options)

    async def _update_statistics_message(
        self, guild_id: str, settings: dict, stats: ConfrerieStats
    ):
        message = await self.recap_messages.get(guild_id, settings)
        if message is None:
            raise ConfrerieError("Canal de récapitulatif introuvable ou invalide")

        embed = build_statistics_embed(stats)
        footer = await self._create_embed_footer(guild_id)
        embed.set_footer(text=footer.text, icon_url=footer.icon_url)

        texts_url = settings.get("confrerieTextsUrl") or ""
        if texts_url:
            texts_url = await shorten_keyed(
                f"confrerie-textes-{guild_id}",
                texts_url,
                title="Textes de la confrérie",
                tags=["confrerie"],
            )
            content = (
                f"Retrouvez tous les textes en [cliquant ici]({texts_url} 'Notion de la confrérie')"
            )
        else:
            content = ""
        try:
            await edit_message_if_changed(
                message,
                content=content,
                embed=embed,
                ignore_timestamp=True,
                logger=logger,
            )
        except Exception:
            # Deleted message or lost access: fetch (or recreate) it next cycle.
            self.recap_messages.forget(guild_id)
            raise
