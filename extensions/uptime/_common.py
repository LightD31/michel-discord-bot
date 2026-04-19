"""Shared config, logger and constants for the Uptime extension."""

import os

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

config, _module_config, enabled_servers = load_config("moduleUptime")


def has_kuma_credentials() -> bool:
    kuma = config.get("uptimeKuma", {})
    return bool(
        kuma.get("uptimeKumaUrl")
        and kuma.get("uptimeKumaUsername")
        and kuma.get("uptimeKumaPassword")
    )
