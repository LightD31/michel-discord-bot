"""
CompareAI Extension — Michel's AI answers.

- ``/ask`` compares several models side by side (anonymous answers, any member
  votes, models revealed when the vote closes) or gives a quick single-model
  answer.
- ``/ask-stats`` ranks the models by votes.
- Opt-in per guild: Michel answers when @mentioned or replied to.

Models are reached through OpenAI-compatible APIs (OpenRouter, NanoGPT — see
``src.integrations.llm``); the domain logic (prompt, rendering, stats,
persistence) lives in ``features.ai``. The class is a mixin composition:
``ask`` (command + shared answer pipeline), ``mentions``, ``voting``.
"""

from interactions import Client, Extension, listen

from features.ai import AiClient, Cooldown, RecentAnswers
from src.core.errors import DatabaseError
from src.integrations import llm

from ._common import (
    SCOPE_GLOBAL,
    SCOPE_GUILD,
    enabled_guild_ids,
    global_settings,
    logger,
    repo_for,
)
from .ask import AskMixin
from .mentions import MentionsMixin
from .voting import VotingMixin


class CompareAIExtension(AskMixin, MentionsMixin, VotingMixin, Extension):
    """Discord extension for Michel's AI answers."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.ai_client = AiClient()
        self.recent_answers = RecentAnswers()
        self.mention_cooldown = Cooldown()

    @listen()
    async def on_startup(self) -> None:
        settings = global_settings()
        for problem in settings.problems:
            logger.warning("AI config: %s", problem)
        apis = llm.configured_apis()
        if not apis:
            logger.warning("No AI API configured (OpenRouter/NanoGPT baseUrl + key): /ask disabled")
        for name in apis:
            api = await llm.get_api(name)
            if api is not None:
                await api.load_prices()

        for gid in [*enabled_guild_ids(), None]:
            try:
                await repo_for(SCOPE_GUILD if gid else SCOPE_GLOBAL, gid).ensure_indexes()
            except DatabaseError as e:
                logger.error("Could not create AI indexes for %s: %s", gid or "global", e)

        if not self.close_due_comparisons.running:
            self.close_due_comparisons.start()

    async def async_drop(self) -> None:
        """Dashboard unload/reload: close the API clients' connection pools."""
        await llm.close_all()


def setup(bot: Client):
    CompareAIExtension(bot)
