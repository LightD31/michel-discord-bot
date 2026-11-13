"""Shared config and constants for the Tricount extension."""

import os

from interactions import Permissions

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

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
DEFAULT_CURRENCY = "€"
# Ceiling on one expense, reimbursement or recurrence: past it, a typo (or a
# prank) would swamp a group's balances.
MAX_AMOUNT = 100_000


@register_module("moduleTricount")
class TricountConfig(SchemaBase):
    __label__ = "Tricount"
    __description__ = "Gestion des dépenses partagées."
    __icon__ = "💰"
    __category__ = "Outils"

    enabled: bool = enabled_field()
    tricountCurrency: str = ui(
        "Devise",
        "string",
        default=DEFAULT_CURRENCY,
        description="Symbole de devise utilisé dans les embeds et les graphiques.",
    )
    tricountCategories: list[str] = ui(
        "Catégories disponibles",
        "list",
        description=(
            "Catégories proposées dans l'autocomplete de /depense. Vide = liste par défaut."
        ),
        default=DEFAULT_CATEGORIES,
    )


logger = logutil.init_logger(__name__)
TRICOUNT = ModuleConfig("moduleTricount")


def guild_categories(guild_id: str | int) -> list[str]:
    """Per-guild category list, falling back to defaults when the override is empty."""
    cfg = TRICOUNT.get(guild_id)
    custom = cfg.get("tricountCategories") or []
    return list(custom) if custom else list(DEFAULT_CATEGORIES)


def guild_currency(guild_id: str | int) -> str:
    cfg = TRICOUNT.get(guild_id)
    return cfg.get("tricountCurrency") or DEFAULT_CURRENCY


def may_manage_group(ctx, group: dict) -> bool:
    """The group's creator or a server manager: they may fix anyone's entries in it.

    Without them, an expense or a recurrence only its author can change stays
    on the books once that author leaves.
    """
    return ctx.author.id == group.get("creator") or bool(
        ctx.author.has_permission(Permissions.MANAGE_GUILD)
    )
