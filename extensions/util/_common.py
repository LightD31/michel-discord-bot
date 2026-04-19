"""Shared logger, config, module config, and constants for the util extension package.

Kept separate from ``__init__.py`` so submodules (``commands``, ``polls``,
``reminders``) can import from here without triggering an import cycle through
the package root.
"""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleUtils")
class UtilsConfig(SchemaBase):
    __label__ = "Utilitaires"
    __description__ = "Commandes utilitaires : ping, sondages, rappels, suppression de messages."
    __icon__ = "🛠️"
    __category__ = "Outils"

    enabled: bool = enabled_field()


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleUtils")
# Convert strings to integers for Discord snowflake IDs
# Type ignore because Discord IDs are ints but type checker expects Snowflake_Type
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore

# Poll emojis constant
POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# Default poll options
DEFAULT_POLL_OPTIONS = ["Oui", "Non"]
DEFAULT_POLL_EMOJIS = ["👍", "👎"]
