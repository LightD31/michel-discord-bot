import os
import random

from interactions import Client, Extension, listen
from interactions.api.events import MemberAdd, MemberRemove

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleWelcome")


class Welcome(Extension):
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
        logger.info(
            "Member %s joined the server %s", event.member.username, event.guild.name
        )
        if str(event.guild.id) not in enabled_servers:
            logger.info("Server not enabled")
            return
        serv_config = module_config.get(str(event.guild.id), {})

        welcome_messages = serv_config.get(
            "welcomeMessageList",
            ["Bienvenue {mention} !"],
        )
        weights = serv_config.get("welcomeMessageWeights", len(welcome_messages) * [1])
        message = random.choices(welcome_messages, weights=weights)[0]
        filled_message = message.format(
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
        logger.info(
            "Member %s left the server %s", event.member.username, event.guild.name
        )
        if str(event.guild.id) not in enabled_servers:
            logger.info("Server not enabled")
            return
        serv_config : dict = module_config.get(str(event.guild.id), {})
        logger.debug("Message : %s\n, Weights : %s\nChannel : %s",
                    serv_config.get("leaveMessageList"),
                    serv_config.get("leaveMessageWeights"),
                    serv_config.get("welcomeChannelId")
                    )
        leave_messages = serv_config.get(
            "leaveMessageList",
            ["Au revoir **{mention}** !"],
        )
        weights = serv_config.get("leaveMessageWeights", len(leave_messages) * [1])
        message = random.choices(leave_messages, weights=weights)[0]
        filled_message = message.format(
            mention=event.member.username,
        )
        # Get the welcome channel
        channel = event.guild.get_channel(
            serv_config.get("welcomeChannelId") or event.guild.system_channel.id
        )
        # Send the welcome message
        await channel.send(filled_message)
