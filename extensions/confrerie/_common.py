"""Shared config, domain exceptions, and slash-command choice lists."""

import os

from interactions import SlashCommandChoice

from src import logutil
from src.config_manager import load_config
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
    __description__ = "Intégration Notion pour la Confrérie des Traducteurs."
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
        "Salon défis", "channel", description="Salon pour les défis de traduction."
    )
    confrerieNewTextChannelId: str | None = ui(
        "Salon nouveaux textes",
        "channel",
        description="Salon pour les notifications de nouveaux textes.",
    )
    confrerieOwnerId: str | None = ui(
        "ID propriétaire",
        "string",
        description="ID Discord du propriétaire de la confrérie.",
    )


config, module_config, enabled_servers = load_config("moduleConfrerie")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}

NOTION_VERSION = "2025-09-03"


class ConfrerieError(Exception):
    """Base exception for Confrérie extension errors (domain-level)."""


class ValidationError(ConfrerieError):
    """Raised when slash-command input fails validation."""


genres = [
    SlashCommandChoice(name="Art/Beaux livres", value="Art/Beaux livres"),
    SlashCommandChoice(name="Aventure/voyage", value="Aventure/voyage"),
    SlashCommandChoice(name="BD/Manga", value="BD/Manga"),
    SlashCommandChoice(name="Conte", value="Conte"),
    SlashCommandChoice(name="Documentaire", value="Documentaire"),
    SlashCommandChoice(name="Essai", value="Essai"),
    SlashCommandChoice(name="Fantasy", value="Fantasy"),
    SlashCommandChoice(name="Feel good", value="Feel good"),
    SlashCommandChoice(name="Historique", value="Historique"),
    SlashCommandChoice(name="Horreur", value="Horreur"),
    SlashCommandChoice(name="Nouvelles", value="Nouvelles"),
    SlashCommandChoice(name="Poésie", value="Poésie"),
    SlashCommandChoice(name="Roman", value="Roman"),
    SlashCommandChoice(name="Science-fiction", value="Science-fiction"),
]

publics = [
    SlashCommandChoice(name="Adulte", value="Adulte"),
    SlashCommandChoice(name="New Adult", value="New Adult"),
    SlashCommandChoice(name="Young Adult", value="Young Adult"),
]

groupes = [
    SlashCommandChoice(name="Editis", value="Editis"),
    SlashCommandChoice(name="Hachette", value="Hachette"),
    SlashCommandChoice(name="Indépendant", value="Indépendant"),
    SlashCommandChoice(name="Madrigall", value="Madrigall"),
]
