"""
UI configuration schemas for the Web UI.

Each per-server module and each global config section is a Pydantic
``BaseModel`` subclass registered via ``@register_module`` / ``@register_section``.
Fields attach UI metadata via ``Field(json_schema_extra={"ui": ...})`` built by
the ``ui()`` helper.

Per-module configs live alongside their owning module (features/<name>/ or
extensions/<name>/), so this file only owns:
  - the DSL (``SchemaBase``, ``ui``, helpers, decorators)
  - the global config sections (discord, mongodb, …)
  - the ``discord2name`` per-server mapping (no natural owner)

``MODULE_SCHEMAS`` / ``GLOBAL_CONFIG_SCHEMAS`` — the dicts consumed by the
frontend — are built lazily via module ``__getattr__`` so extensions can
import the DSL without triggering a circular import.

Recognised widget types (``type=`` on ``ui()``):
    string, number, boolean, channel, role, message, secret, url,
    list, list:number, dict, messagelist, embedlist, keyvaluemap,
    spotifymap, streamermap, teams, discord2name, models.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

_UNSET = object()


# ── DSL ──────────────────────────────────────────────────────────────


def ui(
    label: str,
    type: str = "string",
    *,
    required: bool = False,
    default: Any = _UNSET,
    description: str = "",
    secret: bool = False,
    hidden: bool = False,
    weight_field: str = "",
    channel_field: str = "",
    variables: str = "",
    key_label: str = "",
    value_label: str = "",
) -> Any:
    """Build a Pydantic ``Field(...)`` carrying UI metadata in ``json_schema_extra['ui']``.

    ``default=_UNSET`` (the sentinel) means "no default declared" — the UI
    omits the ``default`` key and the Pydantic field defaults to ``None``.
    """
    meta: dict[str, Any] = {"label": label, "type": type, "required": required}
    if description:
        meta["description"] = description
    if default is not _UNSET:
        meta["default"] = default
    if secret:
        meta["secret"] = True
    if weight_field:
        meta["weightField"] = weight_field
    if variables:
        meta["variables"] = variables
    if key_label:
        meta["keyLabel"] = key_label
    if value_label:
        meta["valueLabel"] = value_label
    if hidden:
        meta["hidden"] = True
    if channel_field:
        meta["channelField"] = channel_field

    field_default = default if default is not _UNSET else None
    return Field(default=field_default, json_schema_extra={"ui": meta})


class SchemaBase(BaseModel):
    """Base for UI-schema source models; class vars hold module/section metadata."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    __label__: ClassVar[str] = ""
    __description__: ClassVar[str] = ""
    __icon__: ClassVar[str] = ""
    __category__: ClassVar[str] = ""
    __no_toggle__: ClassVar[bool] = False
    __direct_value__: ClassVar[bool] = False


# ── Reusable field fragments (public, used by extensions) ────────────
_HIDDEN_MSG_DESC = "ID interne (géré automatiquement)."


def enabled_field() -> Any:
    """Standard ``enabled`` boolean toggle (default off)."""
    return ui("Activé", "boolean", default=False)


def hidden_message_id(label: str, channel_key: str) -> Any:
    """Hidden ``message`` field for an auto-managed persistent message."""
    return ui(
        label, "message", hidden=True, channel_field=channel_key, description=_HIDDEN_MSG_DESC
    )


def secret_field(label: str, required: bool = False, description: str = "") -> Any:
    """Secret string field (masked in the UI)."""
    return ui(label, "secret", required=required, description=description, secret=True)


# ── Registry + lazy aggregation ──────────────────────────────────────

_MODULE_REGISTRY: dict[str, type[SchemaBase]] = {}
_SECTION_REGISTRY: dict[str, type[SchemaBase]] = {}


def register_module(key: str):
    """Decorator: register a per-server module config class under ``key``."""

    def decorator(cls: type[SchemaBase]) -> type[SchemaBase]:
        _MODULE_REGISTRY[key] = cls
        return cls

    return decorator


def register_section(key: str):
    """Decorator: register a global config section class under ``key``."""

    def decorator(cls: type[SchemaBase]) -> type[SchemaBase]:
        _SECTION_REGISTRY[key] = cls
        return cls

    return decorator


def _fields_of(cls: type[BaseModel]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, info in cls.model_fields.items():
        extra = info.json_schema_extra
        meta = extra.get("ui") if isinstance(extra, dict) else None
        if meta:
            out[name] = dict(meta)
    return out


def _module_dict(cls: type[SchemaBase]) -> dict[str, Any]:
    d: dict[str, Any] = {"label": cls.__label__}
    if cls.__description__:
        d["description"] = cls.__description__
    if cls.__icon__:
        d["icon"] = cls.__icon__
    if cls.__category__:
        d["category"] = cls.__category__
    if cls.__no_toggle__:
        d["noToggle"] = True
    if cls.__direct_value__:
        d["directValue"] = True
    d["fields"] = _fields_of(cls)
    return d


def _section_dict(cls: type[SchemaBase]) -> dict[str, Any]:
    d: dict[str, Any] = {"label": cls.__label__}
    if cls.__icon__:
        d["icon"] = cls.__icon__
    d["fields"] = _fields_of(cls)
    return d


def __getattr__(name: str) -> Any:  # noqa: PLR0911
    """Lazy module attributes: rebuild schema dicts from the current registry.

    Using PEP 562 so the dicts always reflect every registered config at the
    moment they are first imported — typically by ``routes/config.py`` after
    all extensions have loaded.
    """
    if name == "MODULE_SCHEMAS":
        return {k: _module_dict(cls) for k, cls in _MODULE_REGISTRY.items()}
    if name == "GLOBAL_CONFIG_SCHEMAS":
        return {k: _section_dict(cls) for k, cls in _SECTION_REGISTRY.items()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Per-server "module" without an owning extension ──────────────────


@register_module("discord2name")
class Discord2NameConfig(SchemaBase):
    __label__ = "Discord → Prénoms"
    __description__ = "Mapping des IDs Discord vers des prénoms pour ce serveur."
    __icon__ = "👤"
    __category__ = "Outils"
    __no_toggle__ = True
    __direct_value__ = True

    discord2name: dict[str, str] = ui(
        "Membres", "discord2name", description="Associez un prénom à chaque ID Discord."
    )


# ── Global config section schemas ────────────────────────────────────


@register_section("discord")
class DiscordSection(SchemaBase):
    __label__ = "Discord"
    __icon__ = "💬"

    botId: str | None = ui("Bot ID", "string", description="ID de l'application / bot Discord.")
    botToken: str = secret_field("Token du bot", required=True, description="Token du bot Discord.")
    ownerId: str | None = ui(
        "ID propriétaire", "string", description="ID Discord du propriétaire du bot."
    )
    devGuildId: str | None = ui(
        "Serveur de développement", "string", description="ID du serveur de développement."
    )
    devGuildChannelId: str | None = ui(
        "Salon de dev", "channel", description="ID du salon de développement."
    )


@register_section("mongodb")
class MongodbSection(SchemaBase):
    __label__ = "MongoDB"
    __icon__ = "🗃️"

    url: str = secret_field(
        "URL de connexion", required=True, description="URL de connexion MongoDB (mongodb://...)."
    )


@register_section("spotify")
class SpotifySection(SchemaBase):
    __label__ = "Spotify"
    __icon__ = "🎵"

    spotifyClientId: str = ui("Client ID", "string", required=True)
    spotifyClientSecret: str = secret_field("Client Secret", required=True)
    spotifyRedirectUri: str | None = ui(
        "Redirect URI", "url", description="URI de redirection OAuth Spotify."
    )


@register_section("twitch")
class TwitchSection(SchemaBase):
    __label__ = "Twitch"
    __icon__ = "📺"

    twitchClientId: str = ui("Client ID", "string", required=True)
    twitchClientSecret: str = secret_field("Client Secret", required=True)


@register_section("youtube")
class YoutubeSection(SchemaBase):
    __label__ = "YouTube"
    __icon__ = "▶️"

    youtubeApiKey: str = secret_field(
        "Clé API", required=True, description="Clé API YouTube Data v3."
    )


@register_section("notion")
class NotionSection(SchemaBase):
    __label__ = "Notion"
    __icon__ = "📝"

    notionSecret: str = secret_field(
        "Token secret", required=True, description="Token d'intégration Notion."
    )


@register_section("OpenRouter")
class OpenRouterSection(SchemaBase):
    __label__ = "OpenRouter"
    __icon__ = "🤖"

    openrouterApiKey: str = secret_field(
        "Clé API", required=True, description="Clé API OpenRouter."
    )
    modelsToCompare: int = ui(
        "Modèles à comparer",
        "number",
        default=3,
        description="Nombre de modèles IA à comparer par question.",
    )
    models: list[Any] = ui(
        "Modèles IA",
        "models",
        description="Liste des modèles IA disponibles pour la comparaison. "
        "Chaque modèle nécessite un identifiant provider, un model_id OpenRouter et un nom d'affichage.",
    )


@register_section("uptimeKuma")
class UptimeKumaSection(SchemaBase):
    __label__ = "Uptime Kuma"
    __icon__ = "📡"

    uptimeKumaUrl: str = ui(
        "URL", "url", required=True, description="URL de l'instance Uptime Kuma."
    )
    uptimeKumaUsername: str = ui("Nom d'utilisateur", "string", required=True)
    uptimeKumaPassword: str = secret_field("Mot de passe", required=True)
    uptimeKuma2FA: str | None = ui(
        "Code 2FA", "string", description="Code 2FA si activé (optionnel)."
    )
    uptimeKumaToken: str | None = secret_field(
        "Token push", description="Token push pour le statut du bot."
    )
    uptimeKumaApiKey: str | None = secret_field("Clé API", description="Clé API Uptime Kuma.")


@register_section("misc")
class MiscSection(SchemaBase):
    __label__ = "Divers"
    __icon__ = "⚙️"

    dataFolder: str = ui(
        "Dossier de données",
        "string",
        default="data",
        description="Chemin du dossier de données local.",
    )


@register_section("shlink")
class ShlinkSection(SchemaBase):
    __label__ = "Shlink"
    __icon__ = "🔗"

    shlinkApiKey: str = secret_field(
        "Clé API", required=True, description="Clé API Shlink pour raccourcir les URLs."
    )


@register_section("random")
class RandomSection(SchemaBase):
    __label__ = "Random.org"
    __icon__ = "🎲"

    randomOrgApiKey: str = secret_field("Clé API", required=True, description="Clé API Random.org.")


@register_section("SecretSanta")
class SecretSantaSection(SchemaBase):
    __label__ = "Secret Santa (global)"
    __icon__ = "🎅"

    secretSantaFile: str | None = ui(
        "Fichier de données",
        "string",
        description="Chemin du fichier JSON des données Secret Santa.",
        default="data/secretsanta.json",
    )
    secretSantaKey: str | None = secret_field(
        "Clé de chiffrement", description="Clé utilisée pour le chiffrement des assignations."
    )


@register_section("backup")
class BackupSection(SchemaBase):
    __label__ = "Sauvegarde BDD"
    __icon__ = "💾"

    enabled: bool = ui(
        "Activé",
        "boolean",
        default=True,
        description="Activer la sauvegarde automatique quotidienne.",
    )
    backupDir: str = ui(
        "Dossier de sauvegarde",
        "string",
        default="data/backups",
        description="Chemin du dossier où les sauvegardes sont stockées.",
    )
    maxBackups: int = ui(
        "Nombre de sauvegardes",
        "number",
        default=7,
        description="Nombre de sauvegardes à conserver (les plus anciennes sont supprimées).",
    )
    backupHour: int = ui(
        "Heure de sauvegarde",
        "number",
        default=4,
        description="Heure locale à laquelle la sauvegarde quotidienne est effectuée (0-23).",
    )


@register_section("webui")
class WebuiSection(SchemaBase):
    __label__ = "Dashboard Web"
    __icon__ = "🌐"

    enabled: bool = enabled_field()
    host: str = ui(
        "Hôte", "string", default="0.0.0.0", description="Adresse de liaison du serveur web."
    )
    port: int = ui("Port", "number", default=8080)
    baseUrl: str = ui(
        "URL de base",
        "url",
        required=True,
        description="URL publique du dashboard (ex: http://monserveur:8080).",
    )
    clientId: str = ui("Client ID Discord", "string", required=True)
    clientSecret: str = secret_field("Client Secret Discord", required=True)
    developerUserIds: list[str] = ui(
        "Developer User IDs",
        "list",
        required=False,
        description="IDs Discord autorisés à accéder aux pages Extensions et Logs (réservées au développeur).",
    )
