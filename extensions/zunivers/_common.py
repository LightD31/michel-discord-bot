"""Shared config schema, logger, and module-level state for the zunivers extension.

Config key ``moduleZunivers`` (formerly ``moduleColoc``).
"""

from features.coloc.constants import ReminderType, discord_channel_link
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
    journaNormalChannelId: str | None = ui(
        "Salon /journa",
        "channel",
        description=(
            "Salon vers lequel pointe le rappel /journa. Vide = le rappel "
            "affiche `/journa` sans lien."
        ),
    )
    journaHardcoreChannelId: str | None = ui(
        "Salon /journa hardcore",
        "channel",
        description=(
            "Salon vers lequel pointe le rappel /journa en mode hardcore. "
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

GUILD_ID = enabled_servers[0] if enabled_servers else ""

# Channel picker keys, with the URL field each one replaced. The legacy key is
# still read so a config filled in before the switch keeps working; drop the
# fallback once the deployed configs use the picker.
JOURNA_CHANNEL_KEYS: dict[ReminderType, tuple[str, str]] = {
    ReminderType.NORMAL: ("journaNormalChannelId", "journaNormalLink"),
    ReminderType.HARDCORE: ("journaHardcoreChannelId", "journaHardcoreLink"),
}


def journa_link(reminder_type: ReminderType) -> str:
    """Return the configured ``/journa`` channel link ("" when unset)."""
    channel_key, legacy_key = JOURNA_CHANNEL_KEYS[reminder_type]
    channel_id = str(module_config.get(channel_key) or "").strip()
    if channel_id:
        return discord_channel_link(GUILD_ID, channel_id)
    return str(module_config.get(legacy_key) or "").strip()


__all__ = [
    "GUILD_ID",
    "ZuniversConfig",
    "config",
    "enabled_servers",
    "journa_link",
    "logger",
    "module_config",
]
