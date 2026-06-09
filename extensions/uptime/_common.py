"""Shared config, logger and constants for the Uptime extension."""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleUptime")
class UptimeConfig(SchemaBase):
    __label__ = "Uptime"
    __description__ = (
        "Suivi Uptime Kuma : statut des serveurs et notifications de maintenance. "
        "Les capteurs se configurent via /uptime ; identifiants dans la section « Uptime Kuma »."
    )
    __icon__ = "📡"
    __category__ = "Outils"

    enabled: bool = enabled_field()


logger = logutil.init_logger(os.path.basename(__file__))

config, _module_config, enabled_servers = load_config("moduleUptime")


def has_kuma_credentials() -> bool:
    kuma = config.get("uptimeKuma", {})
    return bool(
        kuma.get("uptimeKumaUrl")
        and kuma.get("uptimeKumaUsername")
        and kuma.get("uptimeKumaPassword")
    )
