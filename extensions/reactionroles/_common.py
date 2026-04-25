"""Config schema, logger, and shared constants.

Embed/component builders live in :mod:`features.reactionroles.builders` so
the WebUI route can reuse them without crossing the ``extensions/`` package
boundary.
"""

import os

from features.reactionroles import BUTTON_PREFIX, MAX_ENTRIES, build_components, build_embed
from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module

logger = logutil.init_logger(os.path.basename(__file__))


@register_module("moduleReactionRoles")
class ReactionRolesConfig(SchemaBase):
    __label__ = "Rôles à boutons"
    __description__ = "Menus de rôles auto-attribués via boutons persistants."
    __icon__ = "🎭"
    __category__ = "Communauté"

    enabled: bool = enabled_field()


_, _, enabled_servers = load_config("moduleReactionRoles")
enabled_servers_int = [int(s) for s in enabled_servers]

__all__ = [
    "BUTTON_PREFIX",
    "MAX_ENTRIES",
    "ReactionRolesConfig",
    "build_components",
    "build_embed",
    "enabled_servers",
    "enabled_servers_int",
    "logger",
]
