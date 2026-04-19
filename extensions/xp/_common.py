"""Shared config schema, logger, and module-level state for the XP extension.

Kept separate from ``__init__.py`` so submodules (``leveling``, ``commands``,
``leaderboard``) can import from here without triggering an import cycle
through the package root.
"""

import os

import pytz

from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.embeds import Colors
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
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
    xpMessageId: str | None = hidden_message_id("Message leaderboard", "xpChannelId")
    levelUpMessageList: list[str] = ui(
        "Messages de level-up",
        "messagelist",
        description="Liste de messages avec poids de probabilité.",
        default=["Bravo {mention}, tu as atteint le niveau {lvl} !"],
        weight_field="levelUpMessageWeights",
        variables="{mention}, {lvl}",
    )


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleXp")

EMBED_COLOR = Colors.SUCCESS
TIMEZONE = pytz.timezone("Europe/Paris")

__all__ = [
    "EMBED_COLOR",
    "TIMEZONE",
    "XpConfig",
    "config",
    "enabled_servers",
    "logger",
    "module_config",
]
