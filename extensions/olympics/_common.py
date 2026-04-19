"""Shared constants, logger, config, and MongoDB helpers for the Olympics extension."""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.core.db import mongo_manager
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


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


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleOlympics")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}

# ─── Constantes ───────────────────────────────────────────────────────────────
BASE_URL = "https://www.olympics.com/wmr-owg2026/competition/api/FRA"
MEDALS_URL = f"{BASE_URL}/medals"
MEDALLISTS_URL = f"{BASE_URL}/medallists"
EVENT_MEDALS_URL = f"{BASE_URL}/eventmedals"
SCHEDULE_URL_TEMPLATE = (
    f"{BASE_URL.replace('/competition/', '/schedules/')}/schedule/lite/day/{{date}}"
)

POLL_INTERVAL_MINUTES = 3
COUNTRY_CODE = "FRA"
COUNTRY_NAME = "France"

# MongoDB collection (global – pas lié à un serveur)
_olympics_col = mongo_manager.get_global_collection("olympics_state")

# Headers requis pour l'API Olympics.com (anti-bot)
OLYMPICS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.olympics.com/fr/olympic-games/milan-cortina-2026/medals",
    "Origin": "https://www.olympics.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

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
