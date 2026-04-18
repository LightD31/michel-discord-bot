"""Deprecated: use :mod:`src.core.config`.

Re-export shim for one release so existing imports keep working::

    from src.config_manager import load_config, save_config, ...

New code should import from ``src.core.config`` and prefer the reactive
``config_store`` singleton over the module-level ``load_config()`` helper.
"""

from src.core.config import (  # noqa: F401 — re-exported for backward compat
    CONFIG_PATH,
    ConfigStore,
    config_store,
    load_config,
    load_discord2name,
    load_full_config,
    save_config,
    save_module_field,
)

__all__ = [
    "CONFIG_PATH",
    "ConfigStore",
    "config_store",
    "load_config",
    "load_discord2name",
    "load_full_config",
    "save_config",
    "save_module_field",
]
