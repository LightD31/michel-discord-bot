"""Extension Welcome — messages de bienvenue et d'au revoir personnalisés par serveur."""

import os

from interactions import Client, Extension, listen
from interactions.api.events import MemberAdd, MemberRemove

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


class WelcomeExtension(Extension):
    def __init__(self, bot: Client):
        self.bot = bot
        # Load config

    @listen()
    async def on_member_add(self, event: MemberAdd):
        """
        A listener that sends a message when a member joins a guild.

        Parameters:
        -----------
        event : interactions.Member
            The member that joined the guild.
        """
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
        # Get the welcome channel
        channel = event.guild.get_channel(
            serv_config.get("welcomeChannelId") or event.guild.system_channel.id
        )
        # Send the welcome message
        await channel.send(filled_message)

    @listen()
    async def on_member_remove(self, event: MemberRemove):
        """
        A listener that sends a message when a member leaves a guild.

        Parameters:
        -----------
        event : interactions.Member
            The member that left the guild.
        """
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
        # Get the welcome channel
        channel = event.guild.get_channel(
            serv_config.get("welcomeChannelId") or event.guild.system_channel.id
        )
        # Send the welcome message
        await channel.send(filled_message)
