import os
import re
import string

import emoji
from interactions import Extension, listen
from interactions.api.events import MessageCreate

from src import logutil
from src.utils import load_config

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
            if event.message.guild.id not in module_config.keys():
                return
        # Sanitize the message (remove emojis, custom emojis)
        content = self.sanitize_content(event.message.content.lower()).strip()
        logger.debug("Message content: %s", content)
        # Envoie "Pour Feur." si le message contient "pourquoi" et un point d'interrogation dans la même ligne/phrase ou si le dernier mot est "pourquoi"
        if (
            "pourquoi" in content
            and "?" in content.split("pourquoi")[-1].split("\n")[0].split(".")[0]
        ) or "pourquoi" in self.remove_punctuation(content).split(" ")[-1]:
            await event.message.channel.send("Pour feur.")
            return

        # Envoie "Feur" si le message contient "quoi" et un point d'interrogation dans la même ligne/phrase ou si le dernier mot est "quoi"
        if (
            "quoi" in content
            and "?" in content.split("quoi")[-1].split("\n")[0].split(".")[0]
        ) or "quoi" in self.remove_punctuation(content).split(" ")[-1]:
            await event.message.channel.send("Feur.")

    def sanitize_content(self, content):
        # Remove custom emojis
        content = re.sub(r"<:\w*:\d*>", "", content)
        # Remove emojis
        content = emoji.replace_emoji(content, " ")
        # Remove mentions
        content = re.sub(r"<@\d*>", "", content)
        return content

    def remove_punctuation(self, input_string: str):
        # Make a translator object that will replace all punctuation with None
        translator = str.maketrans("", "", string.punctuation)

        # Use the translator object to remove punctuation from the input string
        return input_string.translate(translator).strip()
