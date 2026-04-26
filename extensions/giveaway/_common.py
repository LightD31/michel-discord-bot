"""Config schema, logger, and shared constants for the giveaway extension."""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(os.path.basename(__file__))

MODULE_KEY = "moduleGiveaway"
DEFAULT_EMOJI = "🎉"
DEFAULT_ALLOW_HOST_WIN = False


@register_module(MODULE_KEY)
class GiveawayConfig(SchemaBase):
    __label__ = "Giveaways"
    __description__ = "Tirages au sort à entrée par réaction avec tirage planifié."
    __icon__ = "🎁"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    giveawayEmoji: str = ui(
        "Emoji d'entrée",
        "string",
        default=DEFAULT_EMOJI,
        description="Emoji unicode utilisé pour participer (par défaut 🎉).",
    )
    allowHostWin: bool = ui(
        "Autoriser l'organisateur à gagner",
        "boolean",
        default=DEFAULT_ALLOW_HOST_WIN,
        description=(
            "Si activé, l'hôte du giveaway peut participer et être tiré au sort "
            "s'il réagit avec l'emoji d'entrée."
        ),
    )


# NOTE: keep the literal string here — the WebUI's ``build_module_to_extension_map``
# scans source files with a regex matching ``load_config("…")``, so a constant
# would make the module invisible to the dashboard's auto-reload.
_, module_config, enabled_servers = load_config("moduleGiveaway")
enabled_servers_int = [int(s) for s in enabled_servers]


def guild_emoji(guild_id: str | int | None) -> str:
    """Resolve the per-guild entry emoji, falling back to the default."""
    if guild_id is None:
        return DEFAULT_EMOJI
    cfg = module_config.get(str(guild_id), {})
    raw = cfg.get("giveawayEmoji")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return DEFAULT_EMOJI


def guild_allow_host_win(guild_id: str | int | None) -> bool:
    """Return whether the giveaway host can be part of the draw pool."""
    if guild_id is None:
        return DEFAULT_ALLOW_HOST_WIN
    cfg = module_config.get(str(guild_id), {})
    raw = cfg.get("allowHostWin")
    if isinstance(raw, bool):
        return raw
    return DEFAULT_ALLOW_HOST_WIN


__all__ = [
    "DEFAULT_EMOJI",
    "GiveawayConfig",
    "DEFAULT_ALLOW_HOST_WIN",
    "MODULE_KEY",
    "guild_allow_host_win",
    "enabled_servers",
    "enabled_servers_int",
    "guild_emoji",
    "logger",
    "module_config",
]
