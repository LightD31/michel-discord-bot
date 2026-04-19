"""Shared config schema, logger, and module-level state for the coloc extension."""

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleColoc")
class ColocConfig(SchemaBase):
    __label__ = "Colocation"
    __description__ = "Gestion de la colocation et notifications Zunivers."
    __icon__ = "🏠"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    colocZuniversChannelId: str | None = ui(
        "Salon Zunivers", "channel", description="Salon pour les notifications Zunivers."
    )


logger = logutil.init_logger("extensions.coloc")

config, module_config, enabled_servers = load_config("moduleColoc")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}

__all__ = [
    "ColocConfig",
    "config",
    "enabled_servers",
    "logger",
    "module_config",
]
