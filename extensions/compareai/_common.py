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
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui

MODULE_KEY = "moduleIA"


@register_module(MODULE_KEY)
class IAConfig(SchemaBase):
    __label__ = "Intelligence Artificielle"
    __description__ = (
        "Réponses de Michel via OpenRouter ou NanoGPT : /ask (comparaison de modèles avec vote, "
        "ou réponse rapide), en option réponses quand on le mentionne, et /rattrapage dans les "
        "salons choisis."
    )
    __icon__ = "🤖"
    __category__ = "Outils"
    __help__ = ModuleHelp(
        "Pose une question à Michel : plusieurs modèles d'IA répondent, et le serveur vote pour la meilleure réponse.",
        ("/ask", "/ask-stats", "/rattrapage"),
        "En option, Michel répond aussi quand on le mentionne, et /rattrapage résume un salon "
        "depuis ton dernier message, là où les administrateurs l'ont activé.",
    )

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
        "surnoms, blagues récurrentes, vocabulaire maison. Reste bref (2000 caractères max) : "
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
        ge=1,
        le=24 * 60,
        default=DEFAULT_VOTE_MINUTES,
        description="Après ce délai, les modèles sont dévoilés et le gagnant affiché.",
    )
    historyLimit: int = ui(
        "Messages de contexte",
        "number",
        ge=0,
        le=50,
        default=DEFAULT_HISTORY_LIMIT,
        description="Nombre de messages récents du salon envoyés comme contexte (0 à 50).",
    )
    maxResponseTokens: int = ui(
        "Longueur max des réponses (tokens)",
        "number",
        ge=50,
        le=4000,
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
        ge=0,
        le=3600,
        default=DEFAULT_MENTION_COOLDOWN,
        description="Par utilisateur. Une mention pendant le délai reçoit la réaction ⏳.",
    )
    catchupChannelIds: list[str] = ui(
        "Salons de /rattrapage",
        "channellist",
        description="Où /rattrapage peut résumer les messages depuis le dernier du membre (200 "
        "au plus). Ces messages sont envoyés au fournisseur d'IA ; chaque résumé compte une "
        "question dans le quota du membre. Vide : /rattrapage n'est disponible nulle part.",
    )
    catchupThreads: bool = ui(
        "/rattrapage dans les fils de ces salons",
        "boolean",
        default=False,
        description="Les fils et posts de forum d'un salon de la liste en profitent aussi. "
        "Leurs messages partent alors chez le fournisseur d'IA comme ceux du salon.",
    )


logger = logutil.init_logger(__name__)

# The scope letter of a vote button's custom id. Comparisons live in the
# guild's database, the only place /ask runs.
SCOPE_GUILD = "g"


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


def repo_for(guild_id: str | int) -> AiComparisonRepository:
    """The comparisons of *guild_id* (its own database)."""
    return AiComparisonRepository(str(guild_id))
