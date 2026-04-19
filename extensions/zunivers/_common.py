"""Shared config schema, logger, and module-level state for the zunivers extension.

Config key ``moduleZunivers`` — migrated from the legacy ``moduleColoc`` key
at startup by :func:`src.core.migrations.migrate_config_module_keys`.
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
