import os
from interactions import Extension, listen
from interactions.api.events import MessageCreate

from src import logutil
from src.utils import load_config, sanitize_content

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleFeur")


class Feur(Extension):
    def _should_respond_pourquoi(self, content: str) -> bool:
        """
        Determines if the bot should respond with "Pour feur." to a message containing "pourquoi".
        
        Args:
            content (str): The sanitized and lowercased message content.
            
        Returns:
            bool: True if the bot should respond, False otherwise.
        """
        if "pourquoi" not in content:
            return False
            
        # Remove punctuation for word boundary analysis
        words = self._extract_words(content)
        
        # Case 1: Message ends with "pourquoi" (with or without punctuation)
        if words and words[-1] == "pourquoi":
            return True
            
        # Case 2: "pourquoi" followed by "?" in the same sentence
        sentences = self._split_into_sentences(content)
        for sentence in sentences:
            if "pourquoi" in sentence and "?" in sentence:
                # Check if "pourquoi" comes before the "?" in this sentence
                pourquoi_index = sentence.find("pourquoi")
                question_mark_index = sentence.find("?", pourquoi_index)
                if question_mark_index != -1:
                    return True
                    
        return False
    
    def _should_respond_quoi(self, content: str) -> bool:
        """
        Determines if the bot should respond with "Feur." to a message containing "quoi".
        
        Args:
            content (str): The sanitized and lowercased message content.
            
        Returns:
            bool: True if the bot should respond, False otherwise.
        """
        if "quoi" not in content:
            return False
            
        # Remove punctuation for word boundary analysis
        words = self._extract_words(content)
        
        # Case 1: Message ends with "quoi" (with or without punctuation)
        if words and words[-1] == "quoi":
            return True
            
        # Case 2: "quoi" followed by "?" in the same sentence
        sentences = self._split_into_sentences(content)
        for sentence in sentences:
            if "quoi" in sentence and "?" in sentence:
                # Check if "quoi" comes before the "?" in this sentence
                quoi_index = sentence.find("quoi")
                question_mark_index = sentence.find("?", quoi_index)
                if question_mark_index != -1:
                    return True
                    
        return False
    
    def _extract_words(self, content: str) -> list:
        """
        Extract words from content, removing punctuation and extra whitespace.
        
        Args:
            content (str): The message content.
            
        Returns:
            list: List of words without punctuation.
        """
        import string
        # Remove punctuation and split into words
        translator = str.maketrans("", "", string.punctuation)
        clean_content = content.translate(translator)
        return clean_content.split()
    
    def _split_into_sentences(self, content: str) -> list:
        """
        Split content into sentences based on common sentence delimiters.
        
        Args:
            content (str): The message content.
            
        Returns:
            list: List of sentences.
        """
        import re
        # Split on sentence-ending punctuation, newlines, or multiple spaces
        sentences = re.split(r'[.!?\n]+|  +', content)
        return [s.strip() for s in sentences if s.strip()]

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
        
        # Check for "pourquoi" wordplay
        if self._should_respond_pourquoi(content):
            await event.message.channel.send("Pour feur.")
            return

        # Check for "quoi" wordplay
        if self._should_respond_quoi(content):
            await event.message.channel.send("Feur.")