"""Shared config, domain exceptions, and slash-command choice lists."""

import os

from interactions import SlashCommandChoice

from src import logutil
from src.config_manager import load_config

logger = logutil.init_logger(os.path.basename(__file__))

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
