"""Shared config schema, logger, and module-level constants for the minecraft extension."""

import os

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import (
    SchemaBase,
    enabled_field,
    hidden_message_id,
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
    minecraftMessageId: str | None = hidden_message_id("Message statut", "minecraftChannelId")
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
        "Mot de passe RCON", description="Mot de passe RCON du serveur."
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
        "Mot de passe SFTP", description="Mot de passe SFTP pour l'accès aux fichiers."
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

config, module_config, enabled_servers = load_config("moduleMinecraft")
module_config = module_config[enabled_servers[0]] if enabled_servers else {}

MINECRAFT_ADDRESS = module_config.get("minecraftUrl", "")
MINECRAFT_IP = module_config.get("minecraftIp", "")
MINECRAFT_PORT = int(module_config.get("minecraftPort", 0))
CHANNEL_ID_KUBZ = module_config.get("minecraftChannelId")
MESSAGE_ID_KUBZ = module_config.get("minecraftMessageId")
MINECRAFT_GUILD_ID = enabled_servers[0] if enabled_servers else None
PIN_STATUS_MESSAGE = bool(module_config.get("minecraftPinMessage", False))
SFTPS_PASSWORD = module_config.get("minecraftSftpsPassword", "")
SFTP_HOST = module_config.get("minecraftSftpHost", MINECRAFT_IP)
SFTP_PORT = int(module_config.get("minecraftSftpPort", 2225))
SFTP_USERNAME = module_config.get("minecraftSftpUsername", "Discord")
MODPACK_NAME = module_config.get("minecraftModpackName", "")
MODPACK_URL = module_config.get("minecraftModpackUrl", "")
MODPACK_VERSION = module_config.get("minecraftModpackVersion", "")
STATUS_URL = module_config.get("minecraftStatusUrl", "")
FOOTER_TEXT = module_config.get("minecraftFooterText", "")
SERVER_TYPE = module_config.get("minecraftServerType", "")

__all__ = [
    "CHANNEL_ID_KUBZ",
    "FOOTER_TEXT",
    "MESSAGE_ID_KUBZ",
    "MINECRAFT_ADDRESS",
    "MINECRAFT_GUILD_ID",
    "MINECRAFT_IP",
    "MINECRAFT_PORT",
    "MODPACK_NAME",
    "MODPACK_URL",
    "MODPACK_VERSION",
    "MinecraftConfig",
    "PIN_STATUS_MESSAGE",
    "SERVER_TYPE",
    "SFTPS_PASSWORD",
    "SFTP_HOST",
    "SFTP_PORT",
    "SFTP_USERNAME",
    "STATUS_URL",
    "config",
    "enabled_servers",
    "logger",
    "module_config",
]
