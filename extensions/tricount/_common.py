"""Shared config, constants, and collection helpers for the Tricount extension."""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.core.db import mongo_manager
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleTricount")
class TricountConfig(SchemaBase):
    __label__ = "Tricount"
    __description__ = "Gestion des dépenses partagées."
    __icon__ = "💰"
    __category__ = "Outils"

    enabled: bool = enabled_field()


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleTricount")

# Default expense categories surfaced in /depense autocomplete. Free-form input
# is also accepted so guilds can use their own taxonomy.
DEFAULT_CATEGORIES = [
    "Alimentation",
    "Logement",
    "Transport",
    "Loisirs",
    "Restaurant",
    "Cadeaux",
    "Voyages",
    "Autre",
]

DEFAULT_CATEGORY = "Autre"


def groups_col(guild_id):
    return mongo_manager.get_guild_collection(str(guild_id), "tricount_groups")


def expenses_col(guild_id):
    return mongo_manager.get_guild_collection(str(guild_id), "tricount_expenses")


def recurring_col(guild_id):
    return mongo_manager.get_guild_collection(str(guild_id), "tricount_recurring")
