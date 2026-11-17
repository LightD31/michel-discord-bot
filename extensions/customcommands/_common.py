"""Shared config schema and logger for the custom-commands extension.

Config key ``moduleCustomCommands``.
"""

from pydantic import BaseModel, ConfigDict, Field

from features.customcommands import MAX_COMMANDS_PER_GUILD
from src.core import logging as logutil
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


class CustomCommandEntry(BaseModel):
    """One entry of the ``commandlist`` widget, as the SPA writes it.

    Shape only: the name rules and the length limits are checked by
    ``features.customcommands.parse_commands``, which tells the admin which
    command it skipped and why.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    responses: list[str] = Field(default_factory=list)


@register_module("moduleCustomCommands")
class CustomCommandsConfig(SchemaBase):
    __label__ = "Commandes personnalisées"
    __description__ = "Commandes slash propres au serveur qui répondent un GIF ou un texte."
    __icon__ = "✨"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    commands: list[CustomCommandEntry] = ui(
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

__all__ = ["CustomCommandEntry", "CustomCommandsConfig", "logger"]
