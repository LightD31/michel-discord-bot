"""Shared config, domain exceptions, and Notion-backed autocomplete helpers."""

import os
from typing import Any

from interactions import AutocompleteContext

from features.confrerie import notion_props as np
from src.core import logging as logutil
from src.core.config import load_config
from src.core.errors import BotError
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
    register_module,
    ui,
)

logger = logutil.init_logger(os.path.basename(__file__))


@register_module("moduleConfrerie")
class ConfrerieConfig(SchemaBase):
    __label__ = "Confrérie"
    __description__ = "Intégration Notion pour la Confrérie de la Plume (textes, défis, éditeurs)."
    __icon__ = "📚"
    __category__ = "Outils"

    enabled: bool = enabled_field()
    confrerieNotionDbOeuvresId: str = ui(
        "Notion DB Œuvres",
        "string",
        required=True,
        description="ID de la base de données Notion pour les œuvres.",
    )
    confrerieNotionDbIdEditorsId: str | None = ui(
        "Notion DB Éditeurs",
        "string",
        description="ID de la base de données Notion pour les éditeurs.",
    )
    confrerieRecapChannelId: str | None = ui(
        "Salon récap",
        "channel",
        description="Salon pour le message de récapitulatif (créé automatiquement).",
    )
    confrerieRecapPinMessage: bool = ui(
        "Épingler le message récap",
        "boolean",
        default=False,
        description="Épingler automatiquement le message de récap.",
    )
    confrerieRecapMessageId: str | None = hidden_message_id(
        "Message récap", "confrerieRecapChannelId"
    )
    confrerieDefiChannelId: str | None = ui(
        "Salon défis",
        "channel",
        description="Salon où sont annoncées les nouvelles participations aux défis.",
    )
    confrerieNewTextChannelId: str | None = ui(
        "Salon nouveaux textes",
        "channel",
        description="Salon où sont annoncés les textes mis à jour (case « Update » cochée).",
    )
    confrerieTextsUrl: str | None = ui(
        "Lien « tous les textes »",
        "url",
        description=(
            "Lien affiché au-dessus du message de récap (Notion de la "
            "confrérie). Vide = aucune phrase d'introduction."
        ),
    )
    confrerieOwnerId: str | None = ui(
        "ID propriétaire",
        "string",
        description="ID Discord du propriétaire de la confrérie.",
    )


# Read once per import. The package ``__init__`` drops these submodules from
# ``sys.modules`` before importing them, so a dashboard reload re-reads the
# config instead of keeping the snapshot taken at bot start.
config, module_config, enabled_servers = load_config("moduleConfrerie")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}
guild_id: str | None = enabled_servers[0] if enabled_servers else None


class ConfrerieError(BotError):
    """Base exception for Confrérie extension errors (domain-level)."""


async def autocomplete_options(
    ctx: AutocompleteContext, notion_client: Any, database_id: str | None, column: str
) -> None:
    """Answer an autocomplete with the options configured on a Notion column.

    Options come from the live data-source schema, so a genre added or renamed
    in Notion shows up here without a code change.
    """
    options: list[str] = []
    if database_id:
        try:
            schema = await notion_client.get_properties_schema(database_id)
            options = np.schema_options(schema, column)
        except Exception as e:  # noqa: BLE001 — an empty list beats a failed interaction
            logger.warning("Options Notion indisponibles pour %s: %s", column, e)
    typed = ctx.input_text.casefold().strip()
    matches = [opt for opt in options if typed in opt.casefold()] if typed else options
    await ctx.send(choices=[{"name": opt[:100], "value": opt[:100]} for opt in matches[:25]])
