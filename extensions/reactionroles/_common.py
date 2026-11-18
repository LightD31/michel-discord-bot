"""Config schema, logger, and shared constants.

Embed/component builders live in :mod:`src.discord_ext.rolemenus` so the
WebUI route can reuse them without crossing the ``extensions/`` package
boundary (and ``features/`` stays free of ``interactions`` imports).
"""

import os

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.rolemenus import BUTTON_PREFIX, MAX_ENTRIES, build_components, build_embed
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module

logger = logutil.init_logger(__name__)


@register_module("moduleReactionRoles")
class ReactionRolesConfig(SchemaBase):
    __label__ = "Rôles à boutons"
    __description__ = "Menus de rôles auto-attribués via boutons persistants."
    __icon__ = "🎭"
    __category__ = "Communauté"
    __help__ = ModuleHelp(
        "Des menus pour choisir ses rôles d'un clic.",
        ("/menu-roles creer", "/menu-roles liste"),
        "Un clic sur un bouton du menu donne le rôle, un second le retire.",
    )

    enabled: bool = enabled_field()


REACTION_ROLES = ModuleConfig("moduleReactionRoles")

__all__ = [
    "BUTTON_PREFIX",
    "MAX_ENTRIES",
    "ReactionRolesConfig",
    "build_components",
    "build_embed",
    "REACTION_ROLES",
    "logger",
]
