"""Shared config schema and logger for the custom-commands extension.

Config key ``moduleCustomCommands``.
"""

from typing import Any

from features.customcommands import MAX_COMMANDS_PER_GUILD
from src.core import logging as logutil
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleCustomCommands")
class CustomCommandsConfig(SchemaBase):
    __label__ = "Commandes personnalisées"
    __description__ = "Commandes slash propres au serveur qui répondent un GIF ou un texte."
    __icon__ = "✨"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    commands: list[Any] = ui(
        "Commandes",
        "commandlist",
        default=[],
        description=(
            "Chaque commande a un nom, une description et une ou plusieurs réponses "
            "(lien de GIF ou texte) tirées au hasard. "
            f"Maximum {MAX_COMMANDS_PER_GUILD} commandes par serveur."
        ),
    )


logger = logutil.init_logger("extensions.customcommands")

__all__ = ["CustomCommandsConfig", "logger"]
