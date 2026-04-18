"""Deprecated: use :mod:`src.core.logging`.

This module is kept as a re-export shim for one release so existing
``from src import logutil`` imports keep working. New code should import
``init_logger``/``get_logger`` directly from ``src.core.logging``.
"""

from src.core.logging import (  # noqa: F401 — re-exported for backward compat
    _CONFIGURED_ATTR,
    CustomFormatter,
    _attach_webui_handler,
    _configure_logger,
    get_logger,
    init_logger,
    overwrite_ipy_loggers,
)

__all__ = [
    "CustomFormatter",
    "get_logger",
    "init_logger",
    "overwrite_ipy_loggers",
]
