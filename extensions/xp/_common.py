"""Shared config schema, logger, and module-level state for the XP extension.

Kept separate from ``__init__.py`` so submodules (``leveling``, ``commands``,
``leaderboard``) can import from here without triggering an import cycle
through the package root.
"""

import os
from typing import Literal

from features.xp import LEVEL_UP_DESTINATIONS, MAX_LEVEL_UP_EVERY, MIN_LEVEL_UP_EVERY
from features.xp.seasons import MAX_MONTHS, MIN_MONTHS
from src.core import clock
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.embeds import Colors
from src.webui.schemas import (
    SchemaBase,
    WeightedMessage,
    enabled_field,
    register_module,
    ui,
)


@register_module("moduleXp")
class XpConfig(SchemaBase):
    __label__ = "Système d'XP"
    __description__ = "Système de niveaux et d'expérience."
    __icon__ = "⭐"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    xpChannelId: str | None = ui(
        "Salon leaderboard",
        "channel",
        description="Salon pour le leaderboard permanent (créé automatiquement).",
    )
    xpPinMessage: bool = ui(
        "Épingler le leaderboard",
        "boolean",
        default=False,
        description="Épingler automatiquement le message du leaderboard.",
    )
    publicDashboard: bool = ui(
        "Dashboard public",
        "boolean",
        default=False,
        description=(
            "Publier les statistiques XP (classement, activité) sur une page "
            "accessible sans connexion, via un lien secret (bouton « Page publique »)."
        ),
    )
    voiceXpEnabled: bool = ui(
        "XP vocal",
        "boolean",
        default=False,
        description="Octroyer de l'XP aux membres présents en salon vocal (par minute).",
    )
    levelRewards: dict[str, str] = ui(
        "Récompenses par niveau",
        "keyvaluemap",
        description="Rôle attribué automatiquement quand un membre atteint ce niveau.",
        key_label="Niveau",
        value_label="Rôle",
        role_use="grant",
    )
    stackLevelRewards: bool = ui(
        "Cumuler les rôles de niveau",
        "boolean",
        default=False,
        description=(
            "Si désactivé, le rôle de niveau précédent est retiré quand un palier "
            "supérieur est franchi."
        ),
    )
    seasonsEnabled: bool = ui(
        "Saisons",
        "boolean",
        default=False,
        description=(
            "Un classement qui repart à zéro chaque saison : `/leaderboard` montre la saison en "
            "cours (le classement de toujours reste disponible), et le podium est publié à la fin."
        ),
    )
    seasonMonths: int = ui(
        "Durée d'une saison (mois)",
        "number",
        ge=MIN_MONTHS,
        le=MAX_MONTHS,
        default=1,
        description="1 : chaque mois, 3 : chaque trimestre, 12 : chaque année (depuis janvier).",
    )
    seasonChannelId: str | None = ui(
        "Salon du podium",
        "channel",
        description="Où publier le podium de fin de saison (sinon le salon du leaderboard).",
    )
    seasonChampionRoleId: str | None = ui(
        "Rôle du champion",
        "role",
        description=(
            "Rôle donné au premier de la saison qui se termine, et retiré au champion précédent."
        ),
        role_use="grant",
    )
    levelUpDestination: Literal["here", "channel", "dm", "off"] = ui(
        "Où annoncer les niveaux",
        "choice",
        default="here",
        choices=LEVEL_UP_DESTINATIONS,
        description=(
            "Là où le membre parlait (le salon du message, ou le chat du salon vocal), dans un "
            "salon dédié, en message privé, ou nulle part. Les rôles de niveau sont donnés "
            "dans tous les cas."
        ),
    )
    levelUpChannelId: str | None = ui(
        "Salon des niveaux",
        "channel",
        description="Le salon dédié, quand les niveaux y sont annoncés.",
    )
    levelUpEvery: int = ui(
        "Annoncer tous les … niveaux",
        "number",
        ge=MIN_LEVEL_UP_EVERY,
        le=MAX_LEVEL_UP_EVERY,
        default=1,
        description=(
            "1 : chaque niveau, 5 : les niveaux 5, 10, 15… Un niveau qui donne un rôle est "
            "toujours annoncé."
        ),
    )
    levelUpMessageList: list[WeightedMessage] = ui(
        "Messages de level-up",
        "messagelist",
        description="Liste de messages avec poids de probabilité.",
        default=[{"text": "Bravo {mention}, tu as atteint le niveau {lvl} !", "weight": 1}],
        variables="{mention}, {lvl}",
    )


logger = logutil.init_logger(__name__)
XP = ModuleConfig("moduleXp")

EMBED_COLOR = Colors.SUCCESS
TIMEZONE = clock.LOCAL_TZ

__all__ = [
    "EMBED_COLOR",
    "TIMEZONE",
    "XP",
    "XpConfig",
    "logger",
]
