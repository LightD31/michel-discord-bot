"""Shared config schema, logger and settings accessors for the AI extension.

Settings are read from the reactive config store at call time (not snapshotted
at import), so a dashboard edit — a new model, an API key, a persona — applies
on the next question without reloading the extension.
"""

from features.ai import AiComparisonRepository, AiGlobalSettings, GuildAiSettings
from features.ai.prompt import DEFAULT_PERSONA
from features.ai.settings import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MENTION_COOLDOWN,
    DEFAULT_VOTE_MINUTES,
)
from src.core import logging as logutil
from src.core.config import config_store
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

MODULE_KEY = "moduleIA"


@register_module(MODULE_KEY)
class IAConfig(SchemaBase):
    __label__ = "Intelligence Artificielle"
    __description__ = (
        "Réponses de Michel via OpenRouter ou NanoGPT : /ask (comparaison de modèles avec vote, "
        "ou réponse rapide) et, en option, réponses quand on le mentionne."
    )
    __icon__ = "🤖"
    __category__ = "Outils"

    enabled: bool = enabled_field()
    persona: str = ui(
        "Personnalité",
        "text",
        default=DEFAULT_PERSONA,
        description="Identité et ton de Michel. Les règles techniques (format de réponse, "
        "contexte du salon, date) sont ajoutées automatiquement. Vide : personnalité par défaut.",
    )
    serverContext: str | None = ui(
        "Contexte du serveur",
        "text",
        description="Ce que Michel doit savoir du serveur pour être dans le ton : qui est qui, "
        "surnoms, blagues récurrentes, vocabulaire maison. Restez bref (2000 caractères max) : "
        "ce texte est envoyé avec chaque question.",
    )
    compareByDefault: bool = ui(
        "Comparer par défaut",
        "boolean",
        default=True,
        description="Mode de /ask quand l'option « mode » n'est pas précisée : comparaison de "
        "plusieurs modèles avec vote (activé) ou réponse rapide d'un seul modèle.",
    )
    voteDurationMinutes: int = ui(
        "Durée du vote (min)",
        "number",
        default=DEFAULT_VOTE_MINUTES,
        description="Après ce délai, les modèles sont dévoilés et le gagnant affiché.",
    )
    historyLimit: int = ui(
        "Messages de contexte",
        "number",
        default=DEFAULT_HISTORY_LIMIT,
        description="Nombre de messages récents du salon envoyés comme contexte (0 à 50).",
    )
    maxResponseTokens: int = ui(
        "Longueur max des réponses (tokens)",
        "number",
        default=DEFAULT_MAX_TOKENS,
    )
    defaultModel: str | None = ui(
        "Modèle par défaut",
        "aimodel",
        description="Modèle du mode rapide et des mentions sur ce serveur. Vide : le modèle "
        "par défaut global.",
    )
    mentionReplies: bool = ui(
        "Répondre aux mentions",
        "boolean",
        default=False,
        description="Michel répond quand on le mentionne (@Michel) ou qu'on répond à l'une de "
        "ses réponses. Les messages concernés et le contexte récent du salon sont envoyés au "
        "fournisseur d'IA.",
    )
    mentionCooldownSeconds: int = ui(
        "Délai entre deux mentions (s)",
        "number",
        default=DEFAULT_MENTION_COOLDOWN,
        description="Par utilisateur. Une mention pendant le délai reçoit la réaction ⏳.",
    )


logger = logutil.init_logger(__name__)

# Custom-id scopes: where a comparison is stored.
SCOPE_GUILD = "g"
SCOPE_GLOBAL = "x"


def global_settings() -> AiGlobalSettings:
    return AiGlobalSettings.from_config(config_store.get().get("config", {}))


def _module_raw(guild_id: str | int | None) -> dict | None:
    if guild_id is None:
        return None
    servers = config_store.get().get("servers") or {}
    raw = (servers.get(str(guild_id)) or {}).get(MODULE_KEY)
    return raw if isinstance(raw, dict) else None


def module_enabled(guild_id: str | int | None) -> bool:
    raw = _module_raw(guild_id)
    return bool(raw and raw.get("enabled", False))


def guild_settings(guild_id: str | int | None) -> GuildAiSettings:
    """The guild's settings when the module is enabled there, else the defaults."""
    raw = _module_raw(guild_id)
    if not raw or not raw.get("enabled", False):
        return GuildAiSettings()
    return GuildAiSettings.from_config(raw)


def enabled_guild_ids() -> list[str]:
    servers = config_store.get().get("servers") or {}
    return [
        str(gid)
        for gid, server in servers.items()
        if isinstance(server, dict) and (server.get(MODULE_KEY) or {}).get("enabled", False)
    ]


def scope_for(guild_id: str | int | None) -> str:
    """Comparisons live in the guild's database only where the module is enabled."""
    return SCOPE_GUILD if module_enabled(guild_id) else SCOPE_GLOBAL


def repo_for(scope: str, guild_id: str | int | None) -> AiComparisonRepository:
    if scope == SCOPE_GUILD and guild_id is not None:
        return AiComparisonRepository(str(guild_id))
    return AiComparisonRepository(None)
