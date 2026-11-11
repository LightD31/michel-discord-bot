"""Shared config schema, logger and stats embed for the minecraft extension."""

from interactions import BrandColors, Embed, Timestamp, TimestampStyles

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    register_module,
    secret_field,
    ui,
)


@register_module("moduleMinecraft")
class MinecraftConfig(SchemaBase):
    __label__ = "Minecraft"
    __description__ = "Statut et gestion du serveur Minecraft via RCON."
    __icon__ = "⛏️"
    __category__ = "Esport & Jeux"

    enabled: bool = enabled_field()
    minecraftChannelId: str = ui(
        "Salon statut",
        "channel",
        required=True,
        description="Salon pour le message de statut (créé automatiquement).",
    )
    minecraftPinMessage: bool = ui(
        "Épingler le message de statut",
        "boolean",
        default=False,
        description="Épingler automatiquement le message de statut.",
    )
    minecraftUrl: str | None = ui(
        "URL publique", "string", description="Nom de domaine public du serveur Minecraft."
    )
    minecraftIp: str = ui(
        "IP du serveur", "string", required=True, description="Adresse IP du serveur Minecraft."
    )
    minecraftPort: str = ui(
        "Port du serveur", "string", default="25565", description="Port du serveur Minecraft."
    )
    minecraftRconHost: str | None = ui(
        "Hôte RCON", "string", description="Adresse IP pour la connexion RCON."
    )
    minecraftRconPort: int = ui(
        "Port RCON", "number", default=25575, description="Port RCON du serveur."
    )
    minecraftRconPassword: str | None = secret_field(
        "Mot de passe RCON",
        description="Mot de passe RCON du serveur.",
        sent_to=("minecraftRconHost", "minecraftIp", "minecraftRconPort"),
    )
    minecraftSftpHost: str | None = ui(
        "Hôte SFTP",
        "string",
        description="Adresse IP du serveur SFTP (par défaut, même que l'IP du serveur).",
    )
    minecraftSftpPort: int = ui(
        "Port SFTP", "number", default=2225, description="Port du serveur SFTP."
    )
    minecraftSftpUsername: str = ui(
        "Utilisateur SFTP",
        "string",
        default="Discord",
        description="Nom d'utilisateur pour la connexion SFTP.",
    )
    minecraftSftpsPassword: str | None = secret_field(
        "Mot de passe SFTP",
        description="Mot de passe SFTP pour l'accès aux fichiers.",
        sent_to=("minecraftSftpHost", "minecraftIp", "minecraftSftpPort", "minecraftSftpHostKey"),
    )
    minecraftSftpHostKey: str | None = ui(
        "Clé d'hôte SFTP",
        "string",
        description=(
            "Clé publique du serveur SFTP, au format « ssh-ed25519 AAAA… » (une ligne de "
            "ssh-keyscan -p <port> <hôte> convient). Obligatoire : sans elle, le bot refuse "
            "de se connecter et les statistiques ne sont pas mises à jour."
        ),
    )
    minecraftModpackName: str | None = ui(
        "Nom du modpack", "string", description="Nom du modpack Minecraft."
    )
    minecraftModpackUrl: str | None = ui(
        "URL du modpack", "string", description="Lien vers la page du modpack."
    )
    minecraftModpackVersion: str | None = ui(
        "Version du modpack", "string", description="Version actuelle du modpack."
    )
    minecraftStatusUrl: str | None = ui(
        "URL page de statut", "string", description="Lien vers la page de statut du serveur."
    )
    minecraftFooterText: str | None = ui(
        "Texte du footer", "string", description="Texte affiché en bas de l'embed en veille."
    )
    minecraftServerType: str | None = ui(
        "Type de serveur",
        "string",
        description=(
            "Type de serveur affiché dans le titre de l'embed (ex: Forge, Paper, Fabric). "
            "Laisser vide pour ne pas afficher."
        ),
    )


logger = logutil.init_logger("extensions.minecraft")

MINECRAFT = ModuleConfig("moduleMinecraft")


def build_stats_embed(refreshed_at: Timestamp) -> Embed:
    """The "Stats" embed, rendered identically by both tasks that touch it.

    The hourly stats task regenerates the table image; the 30 s status poll
    rebuilds the whole message and has to reproduce this embed byte for byte,
    or every poll would look like a change.

    ``refreshed_at`` is a fixed instant, never ``now()``: Discord renders the
    relative label client-side and keeps "il y a 5 minutes" current on its own,
    so the bot never has to rewrite the message just to advance a clock.
    """
    return Embed(
        title="Stats",
        description=(
            "Actualisé toutes les heures à Xh10\n"
            f"Dernière actualisation : {refreshed_at.format(TimestampStyles.RelativeTime)}"
        ),
        images="attachment://stats.png",
        color=BrandColors.BLURPLE,
        timestamp=refreshed_at,
    )


__all__ = [
    "MINECRAFT",
    "MinecraftConfig",
    "build_stats_embed",
    "logger",
]
