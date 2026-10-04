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

logger = logutil.init_logger(__name__)


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
        description=(
            "ID Discord du propriétaire de la confrérie. Il reçoit en message privé "
            "les fiches à valider (forums, /demande)."
        ),
    )
    confrerieStaffChannelId: str | None = ui(
        "Salon de validation (repli)",
        "channel",
        description=(
            "Salon où envoyer les fiches à valider quand le propriétaire ne peut pas "
            "recevoir de message privé."
        ),
    )
    confrerieTextesForumId: str | None = ui(
        "ID du forum « textes »",
        "string",
        description=(
            "Chaque nouveau fil de ce forum devient une fiche à valider (titre, "
            "auteur·ice, tags de genre/type, lien vers le fil). Vide = désactivé."
        ),
    )
    confrerieDefisForumId: str | None = ui(
        "ID du forum « défis »",
        "string",
        description=(
            "La première réponse de chaque membre dans un fil de défi (texte long, "
            "fichier ou lien) devient une fiche à valider. Vide = désactivé."
        ),
    )
    confrerieAuthorMap: dict[str, str] = ui(
        "Auteur·ices",
        "keyvaluemap",
        default={},
        description=(
            "Associe un ID Discord au nom d'auteur·ice utilisé dans Notion "
            "(colonne « Auteur »). Sans entrée, le pseudo Discord est proposé."
        ),
        key_label="ID Discord",
        value_label="Nom dans Notion",
    )
    confrerieNotionDbMembresId: str | None = ui(
        "Notion DB Répertoire des membres",
        "string",
        description="ID de la base « Répertoire des membres » (liens affichés par /auteur).",
    )
    confrerieNotionDbOutilsId: str | None = ui(
        "Notion DB Boîte à outils",
        "string",
        description="ID de la base « Boîte à outils » (commande /outil). Vide = désactivé.",
    )


# Read once per import; a dashboard reload re-imports this module (see
# ``src.webui.botops``), so the snapshot follows config edits.
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
