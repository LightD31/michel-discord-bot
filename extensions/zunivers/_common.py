"""Shared config schema, logger, and module-level state for the zunivers extension.

Config key ``moduleZunivers`` (formerly ``moduleColoc``).
"""

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleZunivers")
class ZuniversConfig(SchemaBase):
    __label__ = "Zunivers"
    __description__ = "Rappels /journa, événements et récap corporation Zunivers."
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
    colocFesseGifUrl: str | None = ui(
        "GIF /fesse",
        "url",
        description="GIF envoyé par la commande /fesse. Vide = commande sans réponse.",
    )
    colocMassageGifUrl: str | None = ui(
        "GIF /massageducul",
        "url",
        description="GIF envoyé par la commande /massageducul. Vide = commande sans réponse.",
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
