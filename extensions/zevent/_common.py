"""Config, constants, and small shared helpers for the Zevent extension."""

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from features.zevent.stats import DEFAULT_PROGRESS_WEIGHT
from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
    register_module,
    ui,
)

logger = logutil.init_logger(os.path.basename(__file__))


@register_module("moduleZevent")
class ZeventConfig(SchemaBase):
    __label__ = "Zevent"
    __description__ = "Suivi de l'événement Zevent en temps réel (dons, planning, streamers)."
    __icon__ = "🎉"
    __category__ = "Événements"

    enabled: bool = enabled_field()
    zeventChannelId: str = ui(
        "Salon",
        "channel",
        required=True,
        description="Salon où le message de suivi est posté (créé automatiquement).",
    )
    zeventPinMessage: bool = ui(
        "Épingler le message de suivi",
        "boolean",
        default=False,
        description="Épingler automatiquement le message de suivi.",
    )
    zeventMessageId: str | None = hidden_message_id("Message", "zeventChannelId")
    zeventStreamlabsApiUrl: str = ui(
        "URL Streamlabs",
        "url",
        required=True,
        description=(
            "URL de l'API Streamlabs Charity de l'équipe suivie, "
            "ex. https://streamlabscharity.com/api/v1/teams/@<equipe>/<campagne>."
        ),
    )
    zeventTwitchUrl: str | None = ui(
        "Chaîne Twitch de l'événement",
        "url",
        description=(
            "Lien « Regarder sur Twitch » affiché pendant le concert. Vide = aucun lien affiché."
        ),
    )
    zeventStatsApiUrl: str = ui(
        "URL de l'API statistiques",
        "url",
        required=True,
        description=(
            "Base de l'API EvenMoreStats servant le planning et la répartition "
            "LAN/à distance (sans slash final). Vide = planning et LAN indisponibles."
        ),
    )
    zeventStatsEventId: str | None = ui(
        "Identifiant de l'édition",
        "string",
        description=(
            "UUID de l'édition sur l'API statistiques. Vide = détection automatique "
            "(édition en cours, sinon la prochaine)."
        ),
    )
    zeventEventName: str | None = ui(
        "Nom affiché de l'édition",
        "string",
        description=("Titre de l'embed principal. Vide = nom renvoyé par l'API statistiques."),
    )
    zeventEventStartDate: str | None = ui(
        "Début de l'événement",
        "string",
        description=(
            "Forcer la date/heure de début du concert pré-événement "
            "(ISO 8601, ex: 2026-09-03T18:00:00+00:00). "
            "Vide = déduit de l'API statistiques."
        ),
    )
    zeventMainEventStartDate: str | None = ui(
        "Début du Zevent",
        "string",
        description=(
            "Forcer la date/heure de début du Zevent principal (ISO 8601). "
            "Vide = déduit de l'API statistiques."
        ),
    )
    zeventUpdateInterval: int = ui(
        "Intervalle de mise à jour (secondes)",
        "number",
        description="Fréquence de mise à jour du message en secondes. Nécessite un redémarrage.",
        default=30,
    )
    zeventGoalsProgressWeight: float = ui(
        "Poids de la progression (donation goals)",
        "number",
        default=1.0,
        step=0.1,
        description=(
            "Arbitre le classement des « Prochains donation goals » entre notoriété "
            "et imminence. 0 = uniquement les plus gros collecteurs ; 1 = équilibré ; "
            "au-delà, priorité croissante aux objectifs sur le point d'être atteints."
        ),
    )
    zeventMilestoneInterval: int = ui(
        "Intervalle des paliers (dons)",
        "number",
        description="Montant entre chaque notification de palier de dons.",
        default=100000,
    )


config, _module_config, _enabled_servers = load_config("moduleZevent")
_cfg = _module_config.get(_enabled_servers[0], {}) if _enabled_servers else {}


def _parse_event_dt(iso_str: str) -> datetime | None:
    """Parse a configured override, or ``None`` when unset/unparseable."""
    if not iso_str:
        return None
    try:
        parsed = datetime.fromisoformat(iso_str)
    except ValueError:
        logger.warning(f"Zevent: date de configuration illisible ({iso_str!r}), ignorée.")
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


CHANNEL_ID = int(_cfg.get("zeventChannelId") or 0) or None
MESSAGE_ID = _cfg.get("zeventMessageId")
PIN_MESSAGE = bool(_cfg.get("zeventPinMessage", False))
GUILD_ID = _enabled_servers[0] if _enabled_servers else None

API_URL = "https://zevent.fr/api/"
# Planning and the LAN/remote split still come from the same community project
# (zevent.gdoc.fr) the tracker has always used; only its API moved hosts, from
# zevent-api.gdoc.fr (now 404) to an EvenMoreStats-hosted backend. That base URL
# is configured per guild in the Web UI — the site itself is the data's source,
# so that is what the embeds credit.
STATS_API_URL = (_cfg.get("zeventStatsApiUrl") or "").rstrip("/")
STATS_EVENT_ID = _cfg.get("zeventStatsEventId") or ""
EVENT_NAME = _cfg.get("zeventEventName") or ""
# Configured per guild in the Web UI — no team URL is baked into the code.
STREAMLABS_API_URL = _cfg.get("zeventStreamlabsApiUrl", "")
TWITCH_URL = _cfg.get("zeventTwitchUrl", "")

UPDATE_INTERVAL = int(_cfg.get("zeventUpdateInterval", 30))
MILESTONE_INTERVAL = int(_cfg.get("zeventMilestoneInterval", 100000))


def _parse_weight(value: object) -> float:
    """Read the goals ranking weight, falling back on an unusable setting."""
    try:
        weight = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PROGRESS_WEIGHT
    if weight < 0:
        logger.warning(f"Zevent: poids de progression négatif ({weight}), 0 utilisé.")
        return 0.0
    return weight


GOALS_PROGRESS_WEIGHT = _parse_weight(
    _cfg.get("zeventGoalsProgressWeight", DEFAULT_PROGRESS_WEIGHT)
)

# Set only to pin the countdown by hand; otherwise the dates come from the
# stats API's event schedule (`schedule.start` / `schedule_raising.start`).
EVENT_START_OVERRIDE = _parse_event_dt(_cfg.get("zeventEventStartDate") or "")
MAIN_EVENT_START_OVERRIDE = _parse_event_dt(_cfg.get("zeventMainEventStartDate") or "")

# Last resort only: no override configured *and* the stats API unreachable on a
# cold start. Once the API answers, its schedule wins over these.
FALLBACK_EVENT_START = datetime(2026, 9, 3, 18, 0, 0, tzinfo=UTC)
FALLBACK_MAIN_EVENT_START = datetime(2026, 9, 4, 16, 0, 0, tzinfo=UTC)


@dataclass
class StreamerInfo:
    display_name: str
    twitch_name: str
    is_online: bool
    location: str
    donation_amount: float = 0.0
    """Euros raised, used to rank which offline streamers get a slot."""


def split_streamer_list(streamer_list: str, max_length: int = 1024) -> list[str]:
    chunks = []
    current_chunk = []
    current_length = 0
    for streamer in streamer_list.split(", "):
        if current_length + len(streamer) + 2 > max_length:
            chunks.append(", ".join(current_chunk))
            current_chunk = [streamer]
            current_length = len(streamer)
        else:
            current_chunk.append(streamer)
            current_length += len(streamer) + 2

    if current_chunk:
        chunks.append(", ".join(current_chunk))

    return chunks
