"""Shared config schema and constants for the Uptime extension."""

from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleUptime")
class UptimeConfig(SchemaBase):
    __label__ = "Uptime"
    __description__ = (
        "Suivi Uptime Kuma : statut des serveurs et notifications de maintenance. "
        "Les capteurs se configurent via /uptime ; URL et clé API dans la section « Uptime Kuma »."
    )
    __icon__ = "📡"
    __category__ = "Outils"

    enabled: bool = enabled_field()


_config, _module_config, enabled_servers = load_config("moduleUptime")

# How often /metrics is read. Each read uses one of Kuma's 60 API requests a minute,
# shared with anything else using an API key (a Prometheus scraper, for instance).
POLL_SECONDS = 30

# Discord caps autocomplete at 25 choices of up to 100 characters, and an embed at 25 fields.
MAX_CHOICES = 25
MAX_CHOICE_NAME = 100
MAX_EMBED_FIELDS = 25

KUMA_UNREACHABLE = (
    "Uptime Kuma est injoignable pour le moment (URL ou clé API manquante, ou serveur hors "
    "ligne). Réessayez plus tard."
)
