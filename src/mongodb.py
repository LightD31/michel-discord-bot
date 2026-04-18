"""Deprecated: use :mod:`src.core.db`.

Re-export shim for one release so ``from src.mongodb import mongo_manager``
keeps working. New code should import from ``src.core.db``.
"""

from src.core.db import (  # noqa: F401 — re-exported for backward compat
    GLOBAL_DB_NAME,
    GUILD_DB_PREFIX,
    MongoManager,
    mongo_manager,
)

__all__ = ["GLOBAL_DB_NAME", "GUILD_DB_PREFIX", "MongoManager", "mongo_manager"]
