"""Shared constants, logger, config, and MongoDB helpers for the Olympics extension."""

import os
from urllib.parse import urlsplit

from src.core import logging as logutil
from src.core.config import ModuleConfig, global_config
from src.webui.schemas import SchemaBase, enabled_field, register_module, register_section, ui


@register_module("moduleOlympics")
class OlympicsConfig(SchemaBase):
    __label__ = "Jeux Olympiques"
    __description__ = "Alertes médailles des Jeux Olympiques."
    __icon__ = "🏅"
    __category__ = "Événements"

    enabled: bool = enabled_field()
    olympicsChannelId: str = ui(
        "Salon alertes",
        "channel",
        required=True,
        description="Salon pour les alertes de médailles.",
    )
    olympicsLogoUrl: str | None = ui(
        "Logo des embeds",
        "url",
        description=(
            "Vignette affichée sur les embeds médailles (logo officiel de "
            "l'édition). Vide = aucune vignette."
        ),
    )


@register_section("olympics")
class OlympicsSection(SchemaBase):
    """The edition the bot follows on olympics.com: one for every guild."""

    __label__ = "Jeux Olympiques"
    __description__ = "Édition suivie, commune à tous les serveurs où le module est activé."
    __icon__ = "🏅"

    apiUrl: str = ui(
        "URL de l'API",
        "url",
        required=True,
        description=(
            "API des résultats de l'édition sur olympics.com, langue comprise "
            "(https://www.olympics.com/wmr-owg2026/competition/api/FRA). "
            "Vide = ni alertes ni commandes."
        ),
    )
    refererUrl: str | None = ui(
        "Page de référence",
        "url",
        description=(
            "Page olympics.com annoncée comme provenance des requêtes (Referer, et son "
            "origine comme Origin), sans quoi le pare-feu du site peut les refuser "
            "(https://www.olympics.com/fr/olympic-games/milan-cortina-2026/medals)."
        ),
    )


logger = logutil.init_logger(__name__)
OLYMPICS = ModuleConfig("moduleOlympics")


def olympics_config() -> dict:
    """The global ``olympics`` section, read at use."""
    section = global_config().get("olympics")
    return section if isinstance(section, dict) else {}


def olympics_api_url() -> str:
    """The edition's results API, without a trailing slash; empty when unset."""
    return str(olympics_config().get("apiUrl") or "").rstrip("/")


def olympics_headers() -> dict[str, str]:
    """The headers olympics.com expects from a browser on the configured page."""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    referer = olympics_config().get("refererUrl")
    if referer:
        parts = urlsplit(referer)
        headers["Referer"] = referer
        headers["Origin"] = f"{parts.scheme}://{parts.netloc}"
    return headers


# ─── Constantes ───────────────────────────────────────────────────────────────
# Endpoints, relative to the configured API (global « olympics » section).
MEDALS_PATH = "medals"
MEDALLISTS_PATH = "medallists"
EVENT_MEDALS_PATH = "eventmedals"

POLL_INTERVAL_MINUTES = 3
COUNTRY_CODE = "FRA"
COUNTRY_NAME = "France"

# Emojis pour les médailles
MEDAL_EMOJIS = {
    "ME_GOLD": "🥇",
    "ME_SILVER": "🥈",
    "ME_BRONZE": "🥉",
}

MEDAL_LABELS = {
    "ME_GOLD": "Or",
    "ME_SILVER": "Argent",
    "ME_BRONZE": "Bronze",
}

MEDAL_COLORS = {
    "ME_GOLD": 0xFFD700,
    "ME_SILVER": 0xC0C0C0,
    "ME_BRONZE": 0xCD7F32,
}

# Drapeaux des pays fréquents
COUNTRY_FLAGS = {
    "FRA": "🇫🇷",
    "USA": "🇺🇸",
    "GER": "🇩🇪",
    "NOR": "🇳🇴",
    "ITA": "🇮🇹",
    "SWE": "🇸🇪",
    "SUI": "🇨🇭",
    "AUT": "🇦🇹",
    "CAN": "🇨🇦",
    "JPN": "🇯🇵",
    "KOR": "🇰🇷",
    "CHN": "🇨🇳",
    "GBR": "🇬🇧",
    "NED": "🇳🇱",
    "AUS": "🇦🇺",
    "CZE": "🇨🇿",
    "SLO": "🇸🇮",
    "FIN": "🇫🇮",
    "POL": "🇵🇱",
    "ESP": "🇪🇸",
    "BEL": "🇧🇪",
    "RUS": "🇷🇺",
    "BUL": "🇧🇬",
    "ROC": "🏳️",
}

EMBED_COLOR_FRANCE = 0x002395  # Bleu France


def _get_flag(code: str) -> str:
    """Retourne l'émoji drapeau pour un code pays IOC."""
    return COUNTRY_FLAGS.get(code, "🏳️")
