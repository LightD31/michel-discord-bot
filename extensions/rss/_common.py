"""Config schema, logger, and shared constants for the RSS extension."""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(os.path.basename(__file__))

MODULE_KEY = "moduleRss"

# Hard ceiling on entries posted per feed per poll. Protects against feeds that
# rotate their guids and look entirely "new" on a single fetch.
MAX_NEW_PER_POLL = 5

DEFAULT_TEMPLATE = "**{label}** — [{title}]({link})"


@register_module(MODULE_KEY)
class RssConfig(SchemaBase):
    __label__ = "Flux RSS"
    __description__ = (
        "Notifications automatiques pour des flux RSS / Atom — actualités, "
        "jeux gratuits Steam/Epic, subreddits via leur flux .rss, etc."
    )
    __icon__ = "📰"
    __category__ = "Médias & Streaming"

    enabled: bool = enabled_field()
    ChannelId: str = ui(
        "Salon par défaut",
        "channel",
        required=True,
        description="Salon où sont publiées les notifications quand un flux n'a pas de salon dédié.",
    )
    rssFeeds: dict[str, dict] = ui(
        "Flux suivis",
        "rssfeedmap",
        required=True,
        description=(
            "Une entrée par flux. URL obligatoire, libellé optionnel, "
            "salon dédié optionnel (sinon le salon par défaut est utilisé), "
            "modèle de message optionnel — variables `{title}`, `{link}`, "
            "`{summary}`, `{author}`, `{label}`."
        ),
    )
    rssPollMinutes: int = ui(
        "Intervalle de relève (minutes)",
        "number",
        default=15,
        description="Fréquence à laquelle chaque flux est interrogé. Minimum 5.",
    )


_, module_config, enabled_servers = load_config(MODULE_KEY)
enabled_servers_int = [int(s) for s in enabled_servers]


__all__ = [
    "DEFAULT_TEMPLATE",
    "MAX_NEW_PER_POLL",
    "MODULE_KEY",
    "RssConfig",
    "enabled_servers",
    "enabled_servers_int",
    "logger",
    "module_config",
]
