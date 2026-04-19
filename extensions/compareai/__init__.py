"""
CompareAI Extension — Compare responses from multiple AI models.

This extension allows users to ask questions and receive responses from
multiple AI models, then vote for the best response.

The class is assembled as a mixin composition so that each concern lives in
its own module (``ai_client``, ``voting``). Shared data classes, constants,
and helpers are in :mod:`._common`.
"""

from interactions import Client, Extension, listen

from ._common import MessageSplitter, ModelPricing, VoteManager
from .ai_client import CompareAIMixin
from .voting import VotingMixin


class CompareAIExtension(CompareAIMixin, VotingMixin, Extension):
    """Discord extension for comparing AI model responses."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.openrouter_client = None
        self.model_prices: dict[str, ModelPricing] = {}
        self.vote_manager = VoteManager()
        self.message_splitter = MessageSplitter()

    @listen()
    async def on_startup(self) -> None:
        """Initialize the OpenRouter client on bot startup."""
        await self._init_ai_client()


def setup(bot: Client):
    CompareAIExtension(bot)
