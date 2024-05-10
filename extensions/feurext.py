import os
from interactions import Extension, listen
from interactions.api.events import MessageCreate

from src import logutil
from src.utils import load_config, sanitize_content, remove_punctuation

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleFeur")


class Feur(Extension):
    @listen()
    async def on_message(self, event: MessageCreate):
        """
        This method is called when a message is received.

        Args:
            event (interactions.api.events.MessageCreate): The message event.
        """
        if event.message.author.bot is True:
            logger.debug("Message from bot, ignoring")
            return
        if event.message.guild is not None:
            # Don't send if in COLOC
            if str(event.message.guild.id) not in module_config.keys():
                return
        # Sanitize the message (remove emojis, custom emojis)
        content = sanitize_content(event.message.content.lower()).strip()
        logger.debug("Message content: %s", content)
        # Envoie "Pour Feur." si le message contient "pourquoi" et un point d'interrogation dans la même ligne/phrase ou si le dernier mot est "pourquoi"
        if (
            "pourquoi" in content
            and "?" in content.split("pourquoi")[-1].split("\n")[0].split(".")[0]
        ) or "pourquoi" in remove_punctuation(content).split(" ")[-1]:
            await event.message.channel.send("Pour feur.")
            return

        # Envoie "Feur" si le message contient "quoi" et un point d'interrogation dans la même ligne/phrase ou si le dernier mot est "quoi"
        if (
            "quoi" in content
            and "?" in content.split("quoi")[-1].split("\n")[0].split(".")[0]
        ) or "quoi" in remove_punctuation(content).split(" ")[-1]:
            await event.message.channel.send("Feur.")