"""Periodic statistics task: the confrérie recap message, refreshed hourly."""

import os
from datetime import datetime

from interactions import Embed, OrTrigger, Task, TimeTrigger

from features.confrerie import notion_props as np
from features.confrerie.stats import COL_DEFI, ConfrerieStats, aggregate
from features.links import shorten_keyed
from src.core import logging as logutil
from src.core.text import take_within_budget
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import edit_message_if_changed, fetch_or_create_persistent_message

from ._common import ConfrerieError, guild_id, module_config

logger = logutil.init_logger(os.path.basename(__file__))

FIELD_BUDGET = 1024
TOP_ROWS = 10


def _plural(count: int, word: str, plural: str | None = None) -> str:
    # French keeps 0 and 1 in the singular ("0 participation").
    return f"{count} {word if count <= 1 else plural or word + 's'}"


def _lines(entries: list[str], empty: str) -> str:
    kept, _ = take_within_budget(entries[:TOP_ROWS], FIELD_BUDGET, separator="\n")
    return "\n".join(kept) or empty


def build_statistics_embed(stats: ConfrerieStats) -> Embed:
    """Render the recap embed. Pure given ``stats`` so unchanged data re-renders identically."""
    embed = Embed(
        title="Statistiques de la confrérie",
        description=(
            f"**{_plural(stats.total_works, 'œuvre')}** · "
            f"**{_plural(stats.total_defi_entries, 'participation')}** aux défis · "
            f"**{_plural(stats.author_count, 'auteur·ice')}**"
        ),
        color=Colors.CONFRERIE,
        timestamp=datetime.now(),
    )

    embed.add_field(
        name="✍️ Auteur·ices les plus prolifiques",
        value=_lines(
            [
                f"{a.name} : **{_plural(a.works, 'œuvre')}** · {_plural(a.defis, 'défi')}"
                for a in stats.authors
            ],
            "Aucun auteur trouvé",
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 Défis les plus suivis",
        value=_lines(
            [f"{name} : **{_plural(n, 'texte')}**" for name, n in stats.numbered_defis],
            "Aucun défi trouvé",
        ),
        inline=True,
    )
    if stats.series:
        embed.add_field(
            name="📚 Séries",
            value=_lines(
                [f"{name} : **{_plural(n, 'texte')}**" for name, n in stats.series],
                "—",
            ),
            inline=True,
        )
    if stats.latest_defi:
        name, count = stats.latest_defi
        embed.add_field(
            name="🆕 Dernier défi",
            value=f"{name} — {_plural(count, 'participation')}",
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
        """Hourly stats refresh + recap message edit."""
        logger.debug("Début de la tâche de statistiques de la confrérie")
        try:
            stats = await self._fetch_statistics()
            await self._update_statistics_message(stats)
            logger.debug("Statistiques de la confrérie mises à jour avec succès")
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour des statistiques: {e}")

    async def _fetch_statistics(self) -> ConfrerieStats:
        """Query every œuvre (all pages) and aggregate them."""
        db_id = module_config["confrerieNotionDbOeuvresId"]
        pages = await self.notion_client.query_data_source(database_id=db_id)
        try:
            schema = await self.notion_client.get_properties_schema(db_id)
            defi_options = np.schema_options(schema, COL_DEFI)
        except Exception as e:  # noqa: BLE001 — the options only refine "dernier défi"
            logger.warning("Schéma Notion des œuvres indisponible: %s", e)
            defi_options = []
        return aggregate(pages, defi_options)

    async def _update_statistics_message(self, stats: ConfrerieStats):
        if self._recap_message is None:
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
            # Keep the in-memory snapshot in step with the id persisted to disk,
            # so a later refetch finds this message instead of posting another.
            module_config["confrerieRecapMessageId"] = str(self._recap_message.id)

        embed = build_statistics_embed(stats)
        footer = await self._create_embed_footer()
        embed.set_footer(text=footer.text, icon_url=footer.icon_url)

        texts_url = module_config.get("confrerieTextsUrl") or ""
        if texts_url:
            texts_url = await shorten_keyed(
                f"confrerie-textes-{guild_id or 'global'}",
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
                self._recap_message,
                content=content,
                embed=embed,
                ignore_timestamp=True,
                logger=logger,
            )
        except Exception:
            # Deleted message or lost access: fetch (or recreate) it next cycle.
            self._recap_message = None
            raise
