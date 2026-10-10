"""AskMixin — /ask (comparison or quick answer) and the answer pipeline shared with mentions."""

from __future__ import annotations

import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytz
from interactions import (
    AllowedMentions,
    Buckets,
    Button,
    ButtonStyle,
    Client,
    IntegrationType,
    Message,
    OptionType,
    SlashCommandChoice,
    SlashContext,
    auto_defer,
    cooldown,
    slash_command,
    slash_option,
)

from features.ai import (
    AiClient,
    AiComparisonRepository,
    Comparison,
    ComparisonAnswer,
    GuildAiSettings,
    HistoryMessage,
    ModelAnswer,
    ModelConfig,
    RecentAnswers,
    attach_footer,
    build_history,
    build_system_prompt,
    participants_from,
    pick_compare_models,
    question_header,
    question_turn,
    render_comparison_body,
    render_comparison_footer,
    render_single,
    replace_user_mentions,
    resolve_default_model,
    tally,
    usable_models,
)
from src.core.errors import DatabaseError
from src.discord_ext.messages import send_error
from src.integrations import llm

from ._common import global_settings, guild_settings, logger, repo_for, scope_for

PROMPT_TIMEZONE = pytz.timezone("Europe/Paris")
MODE_COMPARE = "comparer"
MODE_QUICK = "rapide"
VOTE_PREFIX = "aivote"
NO_PINGS = AllowedMentions.none()

_USER_MENTION_ID = re.compile(r"<@!?(\d+)>")


def closes_at_ts(closes_at: datetime) -> int:
    """Epoch seconds; Mongo hands datetimes back naive (UTC)."""
    if closes_at.tzinfo is None:
        closes_at = closes_at.replace(tzinfo=UTC)
    return int(closes_at.timestamp())


def render_comparison(comparison: Comparison) -> list[str]:
    """All message chunks for *comparison* in its current state."""
    body = render_comparison_body(
        f"<@{comparison.asker_id}>", comparison.question, [a.content for a in comparison.answers]
    )
    counts = tally([a.key for a in comparison.answers], comparison.votes)
    footer = render_comparison_footer(
        [a.display_name for a in comparison.answers],
        counts,
        closed=comparison.closed,
        closes_at_ts=closes_at_ts(comparison.closes_at),
    )
    return attach_footer(body, footer)


def vote_buttons(scope: str, comparison: Comparison) -> list[Button]:
    return [
        Button(
            label=f"Réponse {i + 1}",
            style=ButtonStyle.SECONDARY,
            custom_id=f"{VOTE_PREFIX}:{scope}:{comparison.id}:{i}",
        )
        for i in range(len(comparison.answers))
    ]


class AskMixin:
    """The /ask command plus the helpers mention replies reuse."""

    bot: Client
    ai_client: AiClient
    recent_answers: RecentAnswers

    # =========================================================================
    # Context gathering
    # =========================================================================

    def _display_name(self, guild_id: Any, user_id: str) -> str | None:
        if guild_id is not None:
            member = self.bot.cache.get_member(guild_id, user_id)
            if member is not None:
                return member.display_name
        user = self.bot.cache.get_user(user_id)
        return user.display_name if user is not None else None

    def _resolve_mentions(self, guild_id: Any, content: str) -> str:
        """``<@id>`` → ``@name`` from the cache (no API calls); unknown ids stay raw."""
        names: dict[str, str] = {}
        for user_id in set(_USER_MENTION_ID.findall(content)):
            name = self._display_name(guild_id, user_id)
            if name:
                names[user_id] = name
        return replace_user_mentions(content, names)

    def _to_history(self, message: Message, comparison_ids: set[str]) -> HistoryMessage:
        reply_to = None
        if message.message_reference is not None:
            referenced = message.get_referenced_message()
            if referenced is not None and referenced.author is not None:
                reply_to = referenced.author.display_name
        return HistoryMessage(
            author=message.author.display_name,
            content=self._resolve_mentions(message._guild_id, message.content or ""),
            is_self=message.author.id == self.bot.user.id,
            is_comparison=str(message.id) in comparison_ids,
            has_attachments=bool(message.attachments),
            reply_to=reply_to,
        )

    async def _gather_history(
        self,
        channel: Any,
        limit: int,
        repo: AiComparisonRepository,
        *,
        before: Any = None,
        extra: list[Message] | None = None,
    ) -> list[HistoryMessage]:
        """Recent channel messages (oldest first), plus *extra* older ones prepended.

        Fetch failures (no access — e.g. a user-installed command in a server
        the bot isn't in) degrade to no context rather than no answer.
        """
        messages: list[Message] = []
        if channel is not None and limit > 0:
            try:
                kwargs = {"before": before} if before is not None else {}
                messages = list(reversed(await channel.fetch_messages(limit=limit, **kwargs)))
            except Exception as e:
                logger.debug("No history for channel %s: %s", getattr(channel, "id", "?"), e)
        if extra:
            seen = {m.id for m in messages}
            messages = [m for m in extra if m.id not in seen] + messages

        own_ids = [str(m.id) for m in messages if m.author.id == self.bot.user.id]
        try:
            comparison_ids = await repo.known_message_ids(own_ids)
        except DatabaseError as e:
            logger.warning("Could not check past comparisons: %s", e)
            comparison_ids = set()
        return [self._to_history(m, comparison_ids) for m in messages]

    def _build_messages(
        self,
        settings: GuildAiSettings,
        *,
        server: str,
        channel: str,
        author: str,
        history: list[HistoryMessage],
        question: str,
    ) -> list[dict[str, str]]:
        system = build_system_prompt(
            settings.persona,
            server=server,
            channel=channel,
            author=author,
            participants=participants_from(history, author),
            today=datetime.now(PROMPT_TIMEZONE).date(),
            server_context=settings.server_context,
        )
        return [
            {"role": "system", "content": system},
            *build_history(history),
            question_turn(author, question),
        ]

    # =========================================================================
    # Answering
    # =========================================================================

    async def _usable_models(self) -> list[ModelConfig]:
        settings = global_settings()
        for problem in settings.problems:
            logger.warning("AI config: %s", problem)
        return usable_models(settings.models, llm.configured_apis())

    async def _answer_once(
        self,
        candidates: list[ModelConfig],
        settings: GuildAiSettings,
        messages: list[dict[str, str]],
    ) -> ModelAnswer | None:
        """One answer from the default model, retrying once on another model if it fails."""
        first = resolve_default_model(
            candidates, settings.default_model, global_settings().default_model
        )
        if first is None:
            return None
        answer = await self.ai_client.complete(first, messages, settings.max_tokens)
        if answer.ok:
            return answer
        logger.warning("Default model %s failed (%s), trying another", first.key, answer.error)
        others = [m for m in candidates if m.key != first.key]
        if not others:
            return None
        answer = await self.ai_client.complete(random.choice(others), messages, settings.max_tokens)
        return answer if answer.ok else None

    async def _send_chunks(
        self, target: Any, chunks: list[str], components: list[Button] | None = None
    ) -> list[Message]:
        """Send *chunks* in order; *components* go on the last one."""
        sent: list[Message] = []
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            sent.append(
                await target.send(
                    chunk,
                    components=components if is_last and components else None,
                    allowed_mentions=NO_PINGS,
                )
            )
        return sent

    # =========================================================================
    # /ask
    # =========================================================================

    @slash_command(
        name="ask",
        description="Pose une question à Michel (comparaison de modèles ou réponse rapide)",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @cooldown(Buckets.USER, 1, 20)
    @auto_defer()
    @slash_option("question", "Ta question", opt_type=OptionType.STRING, required=True)
    @slash_option(
        "mode",
        "Comparer plusieurs modèles (avec vote) ou une réponse rapide",
        opt_type=OptionType.STRING,
        required=False,
        choices=[
            SlashCommandChoice(name="Comparer", value=MODE_COMPARE),
            SlashCommandChoice(name="Rapide", value=MODE_QUICK),
        ],
    )
    async def ask_question(self, ctx: SlashContext, question: str, mode: str | None = None) -> None:
        try:
            await self._handle_ask(ctx, question, mode)
        except Exception as e:
            logger.exception("Unexpected error in /ask: %s", e)
            await send_error(ctx, "Une erreur inattendue s'est produite")

    async def _handle_ask(self, ctx: SlashContext, question: str, mode: str | None) -> None:
        candidates = await self._usable_models()
        if not candidates:
            await send_error(
                ctx, "IA non configurée : aucune API (OpenRouter, NanoGPT) n'est prête"
            )
            return

        guild_id = ctx.guild_id
        settings = guild_settings(guild_id)
        scope = scope_for(guild_id)
        repo = repo_for(scope, guild_id)
        compare = mode == MODE_COMPARE if mode else settings.compare_by_default

        author = ctx.author.display_name
        question = self._resolve_mentions(guild_id, question)
        history = await self._gather_history(ctx.channel, settings.history_limit, repo)
        messages = self._build_messages(
            settings,
            server=ctx.guild.name if ctx.guild else "Messages privés",
            channel=getattr(ctx.channel, "name", None) or "privé",
            author=author,
            history=history,
            question=question,
        )

        if compare and len(candidates) >= 2:
            await self._ask_compare(ctx, question, candidates, settings, messages, scope, repo)
        else:
            await self._ask_quick(ctx, question, candidates, settings, messages)

    async def _ask_quick(
        self,
        ctx: SlashContext,
        question: str,
        candidates: list[ModelConfig],
        settings: GuildAiSettings,
        messages: list[dict[str, str]],
        note: str | None = None,
    ) -> None:
        answer = await self._answer_once(candidates, settings, messages)
        if answer is None:
            await send_error(ctx, "Aucun modèle n'a pu répondre, réessaie dans un instant")
            return
        await self._post_quick(ctx, question, answer, note)

    async def _post_quick(
        self, ctx: SlashContext, question: str, answer: ModelAnswer, note: str | None = None
    ) -> None:
        footer = f"modèle : {answer.model.display_name}" + (f" · {note}" if note else "")
        chunks = render_single(
            answer.content, header=question_header(ctx.author.mention, question), footer=footer
        )
        sent = await self._send_chunks(ctx, chunks)
        self.recent_answers.add(*(str(m.id) for m in sent))

    async def _ask_compare(
        self,
        ctx: SlashContext,
        question: str,
        candidates: list[ModelConfig],
        settings: GuildAiSettings,
        messages: list[dict[str, str]],
        scope: str,
        repo: AiComparisonRepository,
    ) -> None:
        selected = pick_compare_models(candidates, global_settings().models_to_compare)
        answers = [
            a
            for a in await self.ai_client.complete_many(selected, messages, settings.max_tokens)
            if a.ok
        ]
        if not answers:
            await send_error(ctx, "Aucun modèle n'a pu répondre, réessaie dans un instant")
            return
        if len(answers) == 1:
            await self._post_quick(
                ctx, question, answers[0], "les autres modèles n'ont pas répondu"
            )
            return

        random.shuffle(answers)
        comparison = Comparison(
            question=question,
            asker_id=str(ctx.author.id),
            channel_id=str(ctx.channel_id),
            guild_id=str(ctx.guild_id) if ctx.guild_id else None,
            answers=[
                ComparisonAnswer(
                    key=a.model.key,
                    api=a.model.api,
                    model_id=a.model.model_id,
                    display_name=a.model.display_name,
                    content=a.content,
                )
                for a in answers
            ],
            closes_at=datetime.now(UTC) + timedelta(minutes=settings.vote_minutes),
            created_at=datetime.now(UTC),
        )
        try:
            await repo.create(comparison)
        except DatabaseError as e:
            # Voting needs the database; the answers were paid for, so post them anyway.
            logger.error("Could not store comparison, posting without votes: %s", e)
            comparison.closed = True
            await self._send_chunks(ctx, render_comparison(comparison))
            return

        sent = await self._send_chunks(
            ctx, render_comparison(comparison), components=vote_buttons(scope, comparison)
        )
        try:
            await repo.set_message_ids(comparison.id or "", [str(m.id) for m in sent])
        except DatabaseError as e:
            logger.warning("Could not store comparison message ids: %s", e)
