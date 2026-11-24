"""Shared config schema and logger for the custom-commands extension.

Config key ``moduleCustomCommands``.
"""

from pydantic import BaseModel, ConfigDict, Field

from features.customcommands import (
    MAX_COMMANDS_PER_GUILD,
    MAX_COOLDOWN_SECONDS,
    TargetMode,
)
from src.core import logging as logutil
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui


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
    # "optional" / "required" add a "cible" member option.
    target: TargetMode = "none"
    cooldown: int = Field(0, ge=0, le=MAX_COOLDOWN_SECONDS)
    # Who may run it (empty: everyone); the save checks these as staff roles.
    roles: list[str] = Field(default_factory=list)


@register_module("moduleCustomCommands")
class CustomCommandsConfig(SchemaBase):
    __label__ = "Commandes personnalisées"
    __description__ = "Commandes slash propres au serveur qui répondent un GIF ou un texte."
    __icon__ = "✨"
    __category__ = "Communauté"
    __help__ = ModuleHelp(
        "Les commandes propres à ce serveur, choisies par les admins : un texte, un GIF, "
        "parfois un membre à viser (`cible`) : « Hugo fait un câlin à Léa ».",
    )

    enabled: bool = enabled_field()
    commands: list[CustomCommandEntry] = ui(
        "Commandes",
        "commandlist",
        default=[],
        variables="{auteur}, {cible}, {serveur}",
        description=(
            "Chaque commande a un nom, une description et une ou plusieurs réponses "
            "tirées au hasard. Dans une réponse, `{auteur}` est le membre qui lance la commande, "
            "`{cible}` celui qu'il vise (« tout le monde » s'il n'en nomme aucun) et `{serveur}` "
            "le nom du serveur ; un lien d'image (.gif, .png…) s'affiche en grand sous le texte. "
            f"Maximum {MAX_COMMANDS_PER_GUILD} commandes par serveur."
        ),
    )


logger = logutil.init_logger("extensions.customcommands")

__all__ = ["CustomCommandEntry", "CustomCommandsConfig", "logger"]
