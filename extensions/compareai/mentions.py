"""MentionsMixin — Michel answers when @mentioned, or when someone replies to his answer.

Opt-in per guild (``moduleIA.mentionReplies``). Discord gives bots the content
of messages that mention them; the bot also holds the Message Content intent,
which is what lets it read the reply chain and recent channel context.
Guardrails: humans only, no answer to @everyone/@here or role pings, a
per-user cooldown, and every answer is sent with no mentions parsed.
"""

from __future__ import annotations

import contextlib
from typing import Any

from interactions import Client, Message, listen
from interactions.api.events import MessageCreate

from features.ai import (
    AiClient,
    Cooldown,
    GuildAiSettings,
    MentionFacts,
    RecentAnswers,
    mention_question,
    render_single,
)

from ._common import SCOPE_GUILD, guild_settings, logger, repo_for
from .ask import NO_PINGS

# How far up a reply chain Michel reads to follow a conversation.
MAX_REPLY_CHAIN = 5
COOLDOWN_EMOJI = "⏳"
FAILURE_EMOJI = "⚠️"


class MentionsMixin:
    bot: Client
    ai_client: AiClient
    recent_answers: RecentAnswers
    mention_cooldown: Cooldown

    # Provided by AskMixin.
    _usable_models: Any
    _gather_history: Any
    _build_messages: Any
    _answer_once: Any
    _resolve_mentions: Any

    def _bot_role_ids(self, message: Message) -> frozenset[str]:
        guild = message.guild
        me = guild.me if guild is not None else None
        if me is None:
            return frozenset()
        return frozenset(str(role.id) for role in me.roles if role.bot_managed)

    async def _reply_chain(self, message: Message) -> list[Message]:
        """The messages *message* replies to, oldest first (up to MAX_REPLY_CHAIN)."""
        chain: list[Message] = []
        current = message
        for _ in range(MAX_REPLY_CHAIN):
            if current.message_reference is None:
                break
            try:
                referenced = await current.fetch_referenced_message()
            except Exception as e:
                logger.debug("Could not fetch referenced message: %s", e)
                break
            if referenced is None:
                break
            chain.append(referenced)
            current = referenced
        chain.reverse()
        return chain

    @listen(MessageCreate)
    async def on_ai_mention(self, event: MessageCreate) -> None:
        message = event.message
        if message.guild is None or self.bot.user is None or message.author is None:
            return
        guild_id = str(message.guild.id)
        settings = guild_settings(guild_id)
        if not settings.mention_replies:
            return

        reference = message.message_reference
        facts = MentionFacts(
            content=message.content or "",
            bot_id=str(self.bot.user.id),
            author_is_bot=bool(message.author.bot),
            is_webhook=message.webhook_id is not None,
            mentions_everyone=bool(message.mention_everyone),
            replies_to_answer=reference is not None
            and reference.message_id is not None
            and str(reference.message_id) in self.recent_answers,
            bot_role_ids=self._bot_role_ids(message),
        )
        question = mention_question(facts)
        if question is None:
            return

        allowed, warn = self.mention_cooldown.check(
            str(message.author.id), settings.mention_cooldown
        )
        if not allowed:
            if warn:
                await self._react(message, COOLDOWN_EMOJI)
            return

        try:
            await self._answer_mention(message, question, guild_id, settings)
        except Exception as e:
            logger.exception("Mention reply failed: %s", e)
            await self._react(message, FAILURE_EMOJI)

    async def _answer_mention(
        self, message: Message, question: str, guild_id: str, settings: GuildAiSettings
    ) -> None:
        candidates = await self._usable_models()
        if not candidates:
            logger.warning("Mention in guild %s but no AI API is configured", guild_id)
            await self._react(message, FAILURE_EMOJI)
            return

        channel = message.channel
        with contextlib.suppress(Exception):
            await channel.trigger_typing()

        repo = repo_for(SCOPE_GUILD, guild_id)
        history = await self._gather_history(
            channel,
            settings.history_limit,
            repo,
            before=message.id,
            extra=await self._reply_chain(message),
        )
        messages = self._build_messages(
            settings,
            server=message.guild.name,
            channel=getattr(channel, "name", None) or "inconnu",
            author=message.author.display_name,
            history=history,
            question=self._resolve_mentions(guild_id, question),
        )
        answer = await self._answer_once(candidates, settings, messages)
        if answer is None:
            await self._react(message, FAILURE_EMOJI)
            return

        chunks = render_single(answer.content)
        sent = [await channel.send(chunks[0], reply_to=message, allowed_mentions=NO_PINGS)]
        for chunk in chunks[1:]:
            sent.append(await channel.send(chunk, allowed_mentions=NO_PINGS))
        self.recent_answers.add(*(str(m.id) for m in sent))

    async def _react(self, message: Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except Exception as e:
            logger.debug("Could not react %s: %s", emoji, e)
