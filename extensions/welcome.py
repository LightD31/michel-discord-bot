"""Extension Welcome — messages de bienvenue et d'au revoir personnalisés par serveur."""

import os

from interactions import Client, Extension, File, listen
from interactions.api.events import MemberAdd, MemberRemove

from features.welcome import render_welcome_card
from src.core import logging as logutil
from src.core.config import load_config
from src.core.text import pick_weighted_message
from src.discord_ext.autocomplete import is_guild_enabled
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui


@register_module("moduleWelcome")
class WelcomeConfig(SchemaBase):
    __label__ = "Bienvenue"
    __description__ = "Messages de bienvenue et de départ."
    __icon__ = "👋"
    __category__ = "Communauté"

    enabled: bool = enabled_field()
    welcomeChannelId: str = ui(
        "Salon de bienvenue",
        "channel",
        required=True,
        description="Salon où les messages de bienvenue sont envoyés.",
    )
    welcomeImageEnabled: bool = ui(
        "Carte d'accueil illustrée",
        "boolean",
        default=True,
        description="Joindre une image générée avec l'avatar et le pseudo du membre.",
    )
    welcomeMessageList: list[str] = ui(
        "Messages de bienvenue",
        "messagelist",
        description="Liste de messages avec poids de probabilité.",
        default=["Bienvenue {mention} !"],
        weight_field="welcomeMessageWeights",
        variables="{mention}",
    )
    leaveMessageList: list[str] = ui(
        "Messages de départ",
        "messagelist",
        description="Liste de messages de départ avec poids de probabilité.",
        default=["{username} nous a quittés."],
        weight_field="leaveMessageWeights",
        variables="{username}",
    )


logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleWelcome")


async def _fetch_avatar_bytes(member) -> bytes | None:
    """Best-effort download of the member's avatar as PNG bytes.

    interactions.py exposes ``display_avatar`` as an :class:`Asset` whose
    ``fetch()`` method talks to Discord's CDN through the bot's authenticated
    HTTP client — the recommended path. We force the PNG extension so animated
    avatars decode to a still frame.
    """
    asset = getattr(member, "display_avatar", None) or getattr(member, "avatar", None)
    if asset is None:
        return None
    try:
        return await asset.fetch(extension=".png", size=256)
    except Exception as e:
        logger.warning("Could not fetch avatar for %s: %s", getattr(member, "id", "?"), e)
        return None


def _server_name_subtitle(guild_name: str, member_count: int | None) -> str:
    if member_count:
        return f"{guild_name} · {member_count} membres"
    return guild_name


class WelcomeExtension(Extension):
    def __init__(self, bot: Client):
        self.bot = bot
        # Load config

    @listen()
    async def on_member_add(self, event: MemberAdd):
        """A listener that sends a message when a member joins a guild."""
        logger.info("Member %s joined the server %s", event.member.username, event.guild.name)
        if not is_guild_enabled(event.guild.id, enabled_servers):
            logger.info("Server not enabled")
            return
        serv_config = module_config.get(str(event.guild.id), {})

        filled_message = pick_weighted_message(
            serv_config,
            "welcomeMessageList",
            "welcomeMessageWeights",
            "Bienvenue {mention} !",
            mention=event.member.mention,
        )
        channel = event.guild.get_channel(
            serv_config.get("welcomeChannelId") or event.guild.system_channel.id
        )
        files = await self._build_card_files(
            event.member,
            event.guild,
            title="Bienvenue",
            enabled=serv_config.get("welcomeImageEnabled", True),
        )
        await channel.send(filled_message, files=files)

    @listen()
    async def on_member_remove(self, event: MemberRemove):
        """A listener that sends a message when a member leaves a guild."""
        logger.info("Member %s left the server %s", event.member.username, event.guild.name)
        if not is_guild_enabled(event.guild.id, enabled_servers):
            logger.info("Server not enabled")
            return
        serv_config: dict = module_config.get(str(event.guild.id), {})
        logger.debug(
            "Message : %s\n, Weights : %s\nChannel : %s",
            serv_config.get("leaveMessageList"),
            serv_config.get("leaveMessageWeights"),
            serv_config.get("welcomeChannelId"),
        )
        filled_message = pick_weighted_message(
            serv_config,
            "leaveMessageList",
            "leaveMessageWeights",
            "Au revoir **{mention}** !",
            mention=event.member.username,
        )
        channel = event.guild.get_channel(
            serv_config.get("welcomeChannelId") or event.guild.system_channel.id
        )
        files = await self._build_card_files(
            event.member,
            event.guild,
            title="Au revoir",
            enabled=serv_config.get("welcomeImageEnabled", True),
        )
        await channel.send(filled_message, files=files)

    async def _build_card_files(self, member, guild, *, title: str, enabled: bool) -> list[File]:
        if not enabled:
            return []
        try:
            avatar = await _fetch_avatar_bytes(member)
            buffer = render_welcome_card(
                avatar_bytes=avatar,
                username=member.username,
                title=title,
                subtitle=_server_name_subtitle(guild.name, getattr(guild, "member_count", None)),
            )
            return [File(file=buffer, file_name="welcome.png")]
        except Exception as e:
            logger.warning("Could not render welcome card for %s: %s", member.username, e)
            return []
