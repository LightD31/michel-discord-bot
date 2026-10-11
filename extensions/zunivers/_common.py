"""Shared config schema, logger, and module-level state for the zunivers extension.

Config key ``moduleZunivers`` (formerly ``moduleColoc``).
"""

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleZunivers")
class ZuniversConfig(SchemaBase):
    __label__ = "Zunivers"
    __description__ = "Rappels /journa, événements, récap corporation et conseils ZUnivers Ninja."
    __icon__ = "🎲"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    colocZuniversChannelId: str | None = ui(
        "Salon Zunivers", "channel", description="Salon pour les notifications Zunivers."
    )
    journaReminderRoleId: str | None = ui(
        "Rôle rappel /journa",
        "role",
        description="Rôle mentionné quand le /journa quotidien n'a pas été posté à 22h.",
    )
    journaNormalLink: str | None = ui(
        "Lien du salon /journa",
        "url",
        description=(
            "Lien Discord vers le salon où lancer /journa. Vide = le rappel "
            "affiche `/journa` sans lien."
        ),
    )
    journaHardcoreLink: str | None = ui(
        "Lien du salon /journa hardcore",
        "url",
        description=(
            "Lien Discord vers le salon où lancer /journa en mode hardcore. "
            "Vide = le rappel hardcore n'affiche pas de lien."
        ),
    )
    zuniversHardcoreImageUrl: str | None = ui(
        "Image saison hardcore",
        "url",
        description="Image affichée dans l'embed de saison hardcore. Vide = aucune image.",
    )
    ninjaUrl: str | None = ui(
        "URL de l'API ZUnivers Ninja",
        "url",
        description=(
            "Adresse du serveur ZUnivers Ninja que le bot interroge pour /ninja (ex. le nom "
            "du conteneur sur le réseau Docker partagé). Vide = /ninja désactivé."
        ),
    )
    ninjaWebUrl: str | None = ui(
        "URL publique de ZUnivers Ninja",
        "url",
        description=(
            "Interface web de ZUnivers Ninja, accessible aux joueurs. Ajoute aux rappels "
            "/journa un lien vers le plan du joueur. Vide = aucun lien."
        ),
    )


logger = logutil.init_logger("extensions.zunivers")

config, module_config, enabled_servers = load_config("moduleZunivers")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}

__all__ = [
    "ZuniversConfig",
    "config",
    "enabled_servers",
    "logger",
    "module_config",
]
