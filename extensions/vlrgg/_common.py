"""Shared constants, config, logger, dataclasses, and helpers for the vlrgg extension package."""

import os
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from features.vlrgg import CompetitionView, _clean_vlr_text, competition_view, expand_round_name
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui


class TeamEntry(BaseModel):
    """One team of the ``teams`` widget, as the SPA writes it (see :class:`TeamConfig`)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    vlrTeamId: str | None = None
    channelId: str | None = None
    pin: bool = False


# ── Module config ────────────────────────────────────────────────────────────

MAX_SHOWN_MATCHES = 10
DEFAULT_PAST_MATCHES = 6
DEFAULT_UPCOMING_MATCHES = 6
COMPETITION_CHOICES: dict[str, str] = {
    "off": "Ne pas l'afficher",
    "standings": "Classement",
    "bracket": "Arbre",
}


@register_module("moduleVlrgg")
class VlrggConfig(SchemaBase):
    __label__ = "Esport Tracker (VLR.gg)"
    __description__ = "Suivi automatique des matchs d'équipes Valorant via VLR.gg."
    __icon__ = "🎮"
    __category__ = "Esport & Jeux"
    __help__ = ModuleHelp(
        "Les matchs Valorant des équipes suivies (VLR.gg).",
        ("/vlr equipe", "/vlr match", "/vlr classement"),
        "Les matchs à venir, en cours et terminés sont annoncés, avec le classement "
        "ou l'arbre de la compétition si le serveur l'affiche.",
    )

    enabled: bool = enabled_field()
    notificationChannelId: str | None = ui(
        "Salon notifications",
        "channel",
        description="Salon pour les notifications de matchs en direct et résultats.",
    )
    teams: list[TeamEntry] = ui(
        "Équipes suivies",
        "teams",
        description=(
            "Liste des équipes Valorant à suivre. Chaque équipe nécessite un nom et un ID VLR.gg."
        ),
    )
    reminderMinutes: int = ui(
        "Rappel avant match (minutes)",
        "number",
        ge=0,
        le=24 * 60,
        default=15,
        description=(
            "Délai avant le début d'un match pour poster un rappel dans le salon de "
            "notifications (face-à-face récents, maps des deux équipes). 0 = désactivé."
        ),
    )
    pastMatches: int = ui(
        "Derniers matchs affichés",
        "number",
        ge=0,
        le=MAX_SHOWN_MATCHES,
        default=DEFAULT_PAST_MATCHES,
        description="Nombre de résultats dans le planning de chaque équipe. 0 = aucun.",
    )
    upcomingMatches: int = ui(
        "Prochains matchs affichés",
        "number",
        ge=0,
        le=MAX_SHOWN_MATCHES,
        default=DEFAULT_UPCOMING_MATCHES,
        description="Nombre de matchs à venir dans le planning de chaque équipe. 0 = aucun.",
    )
    competitionView: Literal["off", "standings", "bracket"] = ui(
        "Compétition en cours",
        "choice",
        default="off",
        choices=COMPETITION_CHOICES,
        description=(
            "Ajoute au planning de chaque équipe la compétition qu'elle joue : le classement "
            "de sa phase (groupes, phase suisse) ou l'arbre de ses matchs, tour par tour. "
            "Une phase sans classement (playoffs) est montrée en arbre."
        ),
    )


logger = logutil.init_logger(__name__)
VLRGG = ModuleConfig("moduleVlrgg")

# ── Constants ────────────────────────────────────────────────────────────────

SCHEDULE_INTERVAL_MINUTES = 2
LIVE_UPDATE_INTERVAL_MINUTES = 0.5
DEFAULT_REMINDER_MINUTES = 15
# Fenêtre des bilans par map (rappels, /vlr equipe)
MAP_POOL_DAYS = 180
# Un match qui vient de finir peut encore s'afficher « LIVE » quelques secondes
# côté API : on retente le résumé final au cycle live suivant (30 s), au plus N fois.
FINALIZE_MAX_ATTEMPTS = 10

# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class TeamConfig:
    """Configuration d'une équipe à suivre."""

    name: str
    vlr_team_id: str | None = None
    channel_id: str | None = None  # où vit le planning de l'équipe
    pin: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamConfig":
        """Construit depuis un dict de config."""
        return cls(
            name=data.get("name", "Unknown"),
            vlr_team_id=data.get("vlrTeamId") or None,
            channel_id=str(data.get("channelId") or "").strip() or None,
            pin=bool(data.get("pin", False)),
        )

    @property
    def slot(self) -> str:
        """Le message de planning de l'équipe dans ``bot_messages``."""
        return f"vlrgg:team:{self.name}"


def _shown(raw: Any, default: int) -> int:
    try:
        return min(max(int(raw), 0), MAX_SHOWN_MATCHES)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ScheduleDisplay:
    """Ce que montre le planning d'une équipe, d'après la config du serveur."""

    past: int = DEFAULT_PAST_MATCHES
    upcoming: int = DEFAULT_UPCOMING_MATCHES
    competition: CompetitionView = "off"

    @classmethod
    def from_config(cls, srv_config: dict[str, Any]) -> "ScheduleDisplay":
        """Lu à chaque rafraîchissement ; borné comme le schéma (config éditable à la main)."""
        return cls(
            past=_shown(srv_config.get("pastMatches", DEFAULT_PAST_MATCHES), DEFAULT_PAST_MATCHES),
            upcoming=_shown(
                srv_config.get("upcomingMatches", DEFAULT_UPCOMING_MATCHES),
                DEFAULT_UPCOMING_MATCHES,
            ),
            competition=competition_view(srv_config.get("competitionView", "off")),
        )


@dataclass
class PendingFinal:
    """Match terminé dont le message live attend le résumé définitif."""

    message: Any
    match_data: dict[str, Any]
    attempts: int = 0


@dataclass
class TeamState:
    """État de suivi d'une équipe."""

    team_config: TeamConfig
    server_id: str = ""
    notification_channel: Any = None
    reminder_minutes: int = DEFAULT_REMINDER_MINUTES
    ongoing_matches: dict[str, Any] = field(default_factory=dict)
    live_messages: dict[str, Any] = field(default_factory=dict)
    ending_matches: dict[str, PendingFinal] = field(default_factory=dict)


@dataclass
class ServerState:
    """État de suivi d'un serveur."""

    server_id: str
    notification_channel_id: str | None = None
    notification_channel: Any = None
    teams: dict[str, TeamState] = field(default_factory=dict)


# Re-export helpers that originate from features.vlrgg but are used across submodules
__all__ = [
    "VlrggConfig",
    "logger",
    "VLRGG",
    "COMPETITION_CHOICES",
    "DEFAULT_PAST_MATCHES",
    "DEFAULT_UPCOMING_MATCHES",
    "MAX_SHOWN_MATCHES",
    "ScheduleDisplay",
    "SCHEDULE_INTERVAL_MINUTES",
    "LIVE_UPDATE_INTERVAL_MINUTES",
    "DEFAULT_REMINDER_MINUTES",
    "MAP_POOL_DAYS",
    "FINALIZE_MAX_ATTEMPTS",
    "PendingFinal",
    "TeamConfig",
    "TeamState",
    "ServerState",
    "_clean_vlr_text",
    "expand_round_name",
]
