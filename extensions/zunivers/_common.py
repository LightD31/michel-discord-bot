"""Shared config schema, logger, and module-level state for the zunivers extension.

Config key ``moduleZunivers`` (formerly ``moduleColoc``). Also holds the
``/zunivers`` base command every mixin hangs its subcommands on.
"""

from interactions import SlashCommand

from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleZunivers")
class ZuniversConfig(SchemaBase):
    __label__ = "Zunivers"
    __description__ = (
        "Rappels /journa et !request ZeratoR 24/7, événements, récap corporation et "
        "conseils ZUnivers Ninja."
    )
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
    zerator247Url: str | None = ui(
        "Site ZeratoR 24/7",
        "url",
        description=(
            "Adresse du site ZeratoR 24/7 (ex. https://247.zerator.com), dont l'API indique "
            "quand les !request s'ouvrent. Vide = rappels /zunivers 247 désactivés."
        ),
    )
    zerator247TwitchLogin: str | None = ui(
        "Chaîne Twitch ZeratoR 24/7",
        "string",
        default="zerator_247",
        description="Login de la chaîne Twitch où taper !request, lié dans les rappels.",
    )


logger = logutil.init_logger("extensions.zunivers")

config, module_config, enabled_servers = load_config("moduleZunivers")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}

# ZUnivers Ninja is one server for the whole bot: global ``zuniversNinja`` section.
ninja_config: dict = config.get("zuniversNinja") or {}

zunivers_cmd = SlashCommand(
    name="zunivers",
    description="Commandes ZUnivers",
    scopes=guild_scopes(enabled_servers),
)

__all__ = [
    "ZuniversConfig",
    "config",
    "enabled_servers",
    "logger",
    "module_config",
    "ninja_config",
    "zunivers_cmd",
]
