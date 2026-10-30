"""Shared config schema, constants and messages for the LLDAP extension."""

from features.lldap import parse_role_groups
from src.core.config import load_config
from src.integrations.lldap import DEFAULT_DISCORD_ATTRIBUTE, LldapSettings
from src.webui.schemas import SchemaBase, enabled_field, register_module, secret_field, ui


@register_module("moduleLldap")
class LldapConfig(SchemaBase):
    __label__ = "LLDAP"
    __description__ = (
        "Comptes LLDAP pour les membres selon leurs rôles Discord : /register crée le compte "
        "et l'appartenance aux groupes suit les rôles (toutes les 10 minutes, ou /lldap sync). "
        "Côté LLDAP, créez un attribut utilisateur personnalisé de type chaîne pour l'ID "
        "Discord et utilisez un compte membre de lldap_admin."
    )
    __icon__ = "🔐"
    __category__ = "Outils"

    enabled: bool = enabled_field()
    lldapUrl: str = ui(
        "URL de LLDAP",
        "url",
        required=True,
        description=(
            "Adresse de l'interface web de LLDAP, qui sert aussi l'API GraphQL "
            "(par exemple http://lldap:17170)."
        ),
    )
    lldapLdapUrl: str = ui(
        "URL LDAP",
        "url",
        required=True,
        description=(
            "Adresse du serveur LDAP de LLDAP, pour définir les mots de passe "
            "(par exemple ldap://lldap:3890 ; ldaps:// accepté)."
        ),
    )
    lldapBaseDn: str = ui(
        "Base DN",
        "string",
        required=True,
        description="Base DN de LLDAP (LLDAP_LDAP_BASE_DN), par exemple dc=example,dc=com.",
    )
    lldapAdminUsername: str = ui(
        "Compte administrateur",
        "string",
        required=True,
        description="Identifiant d'un compte membre du groupe lldap_admin, par exemple admin.",
    )
    lldapAdminPassword: str = secret_field(
        "Mot de passe administrateur", required=True, description="Mot de passe de ce compte."
    )
    lldapDiscordAttribute: str = ui(
        "Attribut ID Discord",
        "string",
        default=DEFAULT_DISCORD_ATTRIBUTE,
        description=(
            "Attribut utilisateur personnalisé (type chaîne) où LLDAP garde l'ID Discord du "
            "membre. À créer dans LLDAP (Attributs utilisateur) avant d'activer le module."
        ),
    )
    lldapRoleGroups: dict[str, str] = ui(
        "Rôles → groupes LLDAP",
        "keyvaluemap",
        description=(
            "Chaque rôle Discord donne accès à un groupe LLDAP (son ID numérique, visible dans "
            "l'URL de la page du groupe). Un membre ayant l'un de ces rôles peut créer son "
            "compte avec /register ; perdre le rôle le retire du groupe."
        ),
        key_label="ID du rôle",
        value_label="ID du groupe LLDAP",
    )
    lldapServiceName: str | None = ui(
        "Nom du service",
        "string",
        description="Nom affiché aux membres dans la réponse de /register (par défaut LLDAP).",
    )
    lldapPublicUrl: str | None = ui(
        "Lien de connexion",
        "url",
        description=(
            "Page où les membres se connectent et changent leur mot de passe (portail "
            "d'authentification, LLDAP…). Vide : l'URL de LLDAP."
        ),
    )


_config, module_config, enabled_servers = load_config("moduleLldap")


def guild_settings(guild_id: str) -> LldapSettings:
    return LldapSettings.from_section(module_config.get(guild_id, {}))


def guild_role_groups(guild_id: str) -> tuple[dict[int, int], list[str]]:
    return parse_role_groups(module_config.get(guild_id, {}).get("lldapRoleGroups"))


def service_name(guild_id: str) -> str:
    return str(module_config.get(guild_id, {}).get("lldapServiceName") or "").strip() or "LLDAP"


def login_url(guild_id: str, settings: LldapSettings) -> str:
    return str(module_config.get(guild_id, {}).get("lldapPublicUrl") or "").strip() or (
        settings.base_url
    )


SYNC_MINUTES = 10
# Gateway member chunking of a large guild can take a while; past this the guild is skipped.
CHUNK_TIMEOUT_SECONDS = 60

NOT_ENABLED = "Le module LLDAP n'est pas activé sur ce serveur."
NOT_CONFIGURED = (
    "LLDAP n'est pas configuré sur ce serveur (URL, base DN ou compte administrateur manquant)."
)
NO_MAPPING = "Aucun rôle n'est associé à un groupe LLDAP sur ce serveur."
BAD_USERNAME = (
    "Nom d'utilisateur invalide : 3 à 32 caractères parmi les lettres, chiffres, « . », « _ » "
    "et « - », en commençant par une lettre ou un chiffre."
)
BAD_DISCORD_USERNAME = (
    "Ton pseudo Discord ne peut pas servir de nom d'utilisateur : choisis-en un avec l'option "
    "`username` (3 à 32 lettres, chiffres, « . », « _ » ou « - »)."
)
BAD_EMAIL = "Cette adresse e-mail n'est pas valide."
ALREADY_LINKED = "Ton compte Discord est déjà lié à un compte LLDAP."
EMAIL_TAKEN = "Cette adresse e-mail est déjà utilisée par un compte."
USERNAME_TAKEN = (
    "Ce nom d'utilisateur est déjà pris : choisis-en un autre avec l'option `username`."
)
