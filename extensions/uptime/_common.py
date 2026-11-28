"""Shared config schema and constants for the Uptime extension."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core.config import ModuleConfig
from src.webui.schemas import (
    ModuleHelp,
    SchemaBase,
    enabled_field,
    register_module,
    secret_field,
    ui,
)


class UptimeAlertEntry(BaseModel):
    """One alert of the ``uptimealerts`` widget: a monitor, where it posts, how much (P42)."""

    model_config = ConfigDict(extra="forbid")

    monitorId: str = Field(pattern=r"^[0-9]{1,12}$")
    channelId: str = Field(min_length=1)
    mode: Literal["simple", "detailed"] = "detailed"


@register_module("moduleUptime")
class UptimeConfig(SchemaBase):
    __label__ = "Uptime"
    __description__ = (
        "Suivi Uptime Kuma : statut des serveurs et notifications de maintenance. "
        "Les alertes se règlent ici ou avec /uptime, sur l'instance Kuma du serveur."
    )
    __icon__ = "📡"
    __category__ = "Outils"
    __help__ = ModuleHelp(
        "L'état des serveurs suivis par Uptime Kuma.",
        ("/uptime liste", "/uptime configurer"),
        "Une panne, une maintenance ou un retour en ligne est annoncé dans le salon choisi.",
    )

    enabled: bool = enabled_field()
    uptimeKumaUrl: str = ui(
        "URL de l'instance Uptime Kuma",
        "url",
        required=True,
        description=(
            "Un nom d'hôte seul est joint en HTTPS. Le bot ne lit que /metrics : un proxy "
            "d'authentification devant Kuma doit le laisser passer."
        ),
    )
    uptimeKumaApiKey: str = secret_field(
        "Clé API",
        required=True,
        description=(
            "Clé API Uptime Kuma (Paramètres → Clés API). Elle ne permet que de lire /metrics, "
            "d'où le bot tire l'état des capteurs toutes les 30 secondes."
        ),
        sent_to=("uptimeKumaUrl",),
    )
    alerts: list[UptimeAlertEntry] = ui(
        "Alertes de maintenance",
        "uptimealerts",
        description=(
            "Chaque capteur suivi, le salon où annoncer ses pannes, maintenances et retours, "
            "et le détail des annonces. La liste des capteurs vient de l'instance Kuma "
            "ci-dessus, une fois enregistrée."
        ),
    )


UPTIME = ModuleConfig("moduleUptime")

# How often /metrics is read. Each read uses one of Kuma's 60 API requests a minute,
# shared with anything else using an API key (a Prometheus scraper, for instance).
POLL_SECONDS = 30

# Discord caps autocomplete at 25 choices of up to 100 characters, and an embed at 25 fields.
MAX_CHOICES = 25
MAX_CHOICE_NAME = 100
MAX_EMBED_FIELDS = 25

KUMA_UNREACHABLE = (
    "Uptime Kuma est injoignable pour le moment : vérifie l'URL et la clé API du module "
    "Uptime dans le tableau de bord, ou réessaie plus tard si Kuma est hors ligne."
)
