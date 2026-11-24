"""Config schema, logger, and shared constants for the RSS extension."""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from features.rss.render import DEFAULT_TEMPLATE  # shared with the dashboard
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.webui.schemas import (
    ModuleHelp,
    SchemaBase,
    check_template,
    enabled_field,
    register_module,
    register_section,
    ui,
)

logger = logutil.init_logger(__name__)

MODULE_KEY = "moduleRss"

# Hard ceiling on entries posted per feed per poll. Protects against feeds that
# rotate their guids and look entirely "new" on a single fetch.
MAX_NEW_PER_POLL = 5

# How many feed URLs are fetched at once in a poll cycle, across every guild.
MAX_CONCURRENT_FETCHES = 8


class RssFeedEntry(BaseModel):
    """One feed of the ``rssfeedmap`` widget (keyed by feed id), as the SPA writes it.

    The embed color is written back into a color input: six hex digits.
    """

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    label: str | None = None
    channel_id: str | None = None
    template: str | None = None
    mode: Literal["text", "embed"] = "text"
    embed_color: str | None = Field(default=None, pattern=r"^#?[0-9A-Fa-f]{6}$")
    embed_title: str | None = None
    embed_description: str | None = None
    embed_show_image: bool = True
    embed_show_link_button: bool = False
    embed_link_button_label: str | None = None


@register_module(MODULE_KEY)
class RssConfig(SchemaBase):
    __label__ = "Flux RSS"
    __description__ = (
        "Notifications automatiques pour des flux RSS / Atom — actualités, "
        "jeux gratuits Steam/Epic, subreddits via leur flux .rss, etc."
    )
    __icon__ = "📰"
    __category__ = "Médias & Streaming"
    __help__ = ModuleHelp(
        "Les nouveautés de flux RSS (actus, jeux gratuits, subreddits) dans les salons du serveur.",
        ("/rss liste", "/rss tester"),
        "Chaque flux est relu régulièrement, et ses nouveaux articles sont publiés.",
    )

    enabled: bool = enabled_field()
    rssChannelId: str = ui(
        "Salon par défaut",
        "channel",
        required=True,
        description="Salon où sont publiées les notifications quand un flux n'a pas de salon dédié.",
    )
    rssFeeds: dict[str, RssFeedEntry] = ui(
        "Flux suivis",
        "rssfeedmap",
        required=True,
        description=(
            "Une entrée par flux. URL obligatoire, libellé et salon dédié "
            "optionnels. Mode **texte** ou **embed** : en mode embed, "
            "configure la couleur, le titre, la description (multi-ligne), "
            "l'image et un bouton de lien optionnel. Le préréglage "
            "« LootScraper » importe en un clic les flux jeux gratuits Epic, "
            "Amazon Prime, itch.io et Steam. Variables disponibles dans tous "
            "les modèles : `{title}`, `{link}`, `{summary}`, `{author}`, "
            "`{label}`, `{image}`, `{published}`, `{published_short}`, "
            "`{published_long}`, `{published_relative}`, `{published_date}`, "
            "`{published_iso}` (les variantes `published_*` produisent des "
            "timestamps Discord rendus dans le fuseau du lecteur)."
        ),
    )

    @field_validator("rssFeeds")
    @classmethod
    def _plain_placeholders(cls, feeds: dict[str, RssFeedEntry]) -> dict[str, RssFeedEntry]:
        """Every text of a feed is a template (text, embed title, description…)."""
        for feed in feeds.values():
            for value in feed.model_dump().values():
                if isinstance(value, str):
                    check_template(value)
        return feeds

    rssPollMinutes: int = ui(
        "Intervalle de relève (minutes)",
        "number",
        ge=5,
        le=24 * 60,
        default=15,
        description="Fréquence à laquelle chaque flux est interrogé. Minimum 5.",
    )


RSS = ModuleConfig(MODULE_KEY)


@register_section("rss")
class RssSection(SchemaBase):
    """Dashboard-side settings of the RSS module, shared by every guild."""

    __label__ = "Flux RSS"
    __icon__ = "📰"

    lootScraperBaseUrl: str = ui(
        "Flux LootScraper",
        "url",
        description=(
            "Adresse de base des flux LootScraper (https://feed.eikowagenknecht.com/), "
            "utilisée par les préréglages « jeux gratuits » du module Flux RSS. "
            "Vide : préréglages indisponibles."
        ),
    )


__all__ = [
    "DEFAULT_TEMPLATE",
    "MAX_CONCURRENT_FETCHES",
    "MAX_NEW_PER_POLL",
    "MODULE_KEY",
    "RSS",
    "RssConfig",
    "RssSection",
    "logger",
]
