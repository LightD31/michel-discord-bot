"""Config schema, logger, and shared constants for the reaction-roles extension."""

import os

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
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore[misc]

# Namespace for persistent button custom_ids: rrole:{menu_id}:{entry_idx}
BUTTON_PREFIX = "rrole"
# Discord caps action rows at 5 buttons each, max 5 rows per message.
MAX_ENTRIES = 25
