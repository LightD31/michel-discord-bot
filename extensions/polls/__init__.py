"""Polls Discord extension — one ``/sondage``, three kinds of poll.

``/sondage [mode:] [plusieurs-choix:]`` opens a form: the question, the options
one per line, and how long it stays open ("2h", "3j").

- **public** (the default): a native Discord poll. Discord counts the votes,
  shows the results and closes it (1 hour to 32 days); nothing is stored.
- **anonyme**: buttons, the counts public and the voters hidden.
- **classement**: buttons, ranked choice (instant runoff).

Anonymous and ranked polls are stored in MongoDB (``polls`` collection), close
once their ``closes_at`` deadline elapses (``check_closing_polls``), and carry a
"✏️ Modifier" button for their author: the question can always change, the
options until the first vote. Enabled per-guild via ``moduleUtils``.
"""

import re
from datetime import datetime, timedelta

from interactions import (
    Client,
    Extension,
    IntervalTrigger,
    Modal,
    ModalContext,
    OptionType,
    ParagraphText,
    PollMedia,
    ShortText,
    SlashCommandChoice,
    SlashContext,
    Task,
    listen,
    modal_callback,
    slash_command,
    slash_option,
)
from interactions import Poll as DiscordPoll
from interactions.client.errors import Forbidden, NotFound

from features.polls import (
    BUTTON_OPTION_LIMIT,
    NATIVE_ANSWER_LIMIT,
    Poll,
    PollRepository,
    native_poll_hours,
    parse_duration,
    parse_options,
    poll_footer,
)
from src.core import clock, log_context
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.logging import log_failure
from src.core.text import format_discord_timestamp
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import card, ok
from src.discord_ext.messages import copy_embed, edit_message_if_changed, guild_id_of, send_error
from src.discord_ext.scopes import guild_scopes

from .buttons import (
    EDIT_FORM_RE,
    PollButtonsMixin,
    render_results_field,
    update_poll_embed,
    vote_components,
)

logger = logutil.init_logger(__name__)
UTILS = ModuleConfig("moduleUtils")

MODES = {"public": "Public", "anonymous": "Anonyme", "ranked": "Classement"}
NEW_FORM = "poll_new"  # poll_new:{mode}:{multi 1|0}
_NEW_FORM_RE = re.compile(rf"^{NEW_FORM}:({'|'.join(MODES)}):([01])$")
QUESTION_LIMIT = 250  # the embed title holds "🔒 📊 " before it


def _default_poll_seconds(guild_id: str | int | None) -> int | None:
    """Per-guild default auto-close (seconds) when the duration is left empty; None disables it."""
    if guild_id is None:
        return None
    cfg = UTILS.get(guild_id)
    minutes = cfg.get("pollDefaultDurationMinutes")
    try:
        value = int(minutes) if minutes is not None else 0
    except (TypeError, ValueError):
        value = 0
    return value * 60 if value > 0 else None


def _author_name(ctx) -> str:
    """The poll's author as the server shows them (never their id: the footer is public)."""
    return str(getattr(ctx.author, "display_name", None) or ctx.user.username)


def poll_form(mode: str, multi: bool) -> Modal:
    """The form ``/sondage`` opens."""
    public = mode == "public"
    return Modal(
        ShortText(
            label="Question",
            custom_id="question",
            placeholder="Ex. On joue à quoi samedi ?",
            required=True,
            max_length=QUESTION_LIMIT,
        ),
        ParagraphText(
            label="Options, une par ligne" + (" (vide : Oui / Non)" if public else ""),
            custom_id="options",
            placeholder="Pizza\nSushi\nTacos",
            required=not public,
            max_length=1000,
        ),
        ShortText(
            label="Durée (facultatif)",
            custom_id="duration",
            placeholder="Ex. 2h, 3j" + (" — 24 h si vide" if public else ""),
            required=False,
            max_length=20,
        ),
        title=f"Sondage {MODES[mode].lower()}",
        custom_id=f"{NEW_FORM}:{mode}:{int(multi)}",
    )


# Every sweep a due poll's message can't be read (Discord 5xx, timeout) counts;
# after this many in a row it closes without its results: 10 minutes at one
# sweep every 30 s.
MAX_CLOSE_FAILURES = 20


class _Retry:
    """The poll's message couldn't be read this sweep: try again at the next one."""


RETRY = _Retry()


class PollsExtension(Extension, PollButtonsMixin):
    MODULE_KEYS = ("moduleUtils",)

    def __init__(self, bot: Client):
        self.bot = bot
        self._close_failures: dict[str, int] = {}  # poll id → failed sweeps in a row

    def _poll_repo(self, guild_id: str | int) -> PollRepository:
        return PollRepository(str(guild_id))

    @listen()
    async def on_startup(self) -> None:
        for guild_id in UTILS.guild_ids():
            try:
                await self._poll_repo(guild_id).ensure_indexes()
            except Exception as e:
                log_failure(logger, "Could not init poll indexes for %s: %s", guild_id, e)
        if not self.check_closing_polls.running:
            self.check_closing_polls.start()

    @slash_command(
        name="sondage",
        description="Créer un sondage : un formulaire demande la question et les options",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "mode",
        "Public (défaut), anonyme ou par classement",
        opt_type=OptionType.STRING,
        required=False,
        choices=[
            SlashCommandChoice(name="Public : Discord compte les votes", value="public"),
            SlashCommandChoice(
                name="Anonyme : personne ne voit qui a voté quoi", value="anonymous"
            ),
            SlashCommandChoice(name="Classement : chacun range les options", value="ranked"),
        ],
    )
    @slash_option(
        "plusieurs-choix",
        "Sondage public : chacun peut cocher plusieurs options",
        opt_type=OptionType.BOOLEAN,
        required=False,
        argument_name="multi",
    )
    async def poll(self, ctx: SlashContext, mode: str = "public", multi: bool = False) -> None:
        await ctx.send_modal(poll_form(mode if mode in MODES else "public", multi))

    @modal_callback(_NEW_FORM_RE)
    async def on_poll_form(
        self, ctx: ModalContext, question: str = "", options: str = "", duration: str = ""
    ) -> None:
        """The form's answer: post the poll the mode in its custom id names."""
        match = _NEW_FORM_RE.match(ctx.custom_id)
        if not match:
            return
        mode, multi = match.groups()
        question = question.strip()
        if not question:
            await send_error(ctx, "Écris la question du sondage.")
            return
        try:
            choices = parse_options(
                options,
                limit=NATIVE_ANSWER_LIMIT if mode == "public" else BUTTON_OPTION_LIMIT,
                default_yes_no=mode == "public",
            )
        except ValueError as e:
            await send_error(ctx, str(e))
            return
        seconds: int | None = None
        if duration.strip():
            seconds = parse_duration(duration)
            if seconds is None:
                await send_error(
                    ctx,
                    f"Je n'ai pas compris la durée « {duration.strip()} ». "
                    "Exemples : `30m`, `2h`, `3j`.",
                    icon="⏱️",
                )
                return
        else:
            seconds = _default_poll_seconds(guild_id_of(ctx))
        if mode == "public":
            await self._post_native_poll(ctx, question, choices, seconds, multi == "1")
        else:
            await self._create_button_poll(ctx, question, choices, seconds, mode)

    async def _post_native_poll(
        self, ctx, question: str, options: list[str], seconds: int | None, multi: bool
    ) -> None:
        """A Discord poll: Discord counts, shows and closes it; Michel keeps nothing."""
        answers: list[PollMedia | str] = list(options)
        poll = DiscordPoll.create(
            question,
            duration=native_poll_hours(seconds),
            allow_multiselect=multi,
            answers=answers,
        )
        await ctx.send(poll=poll)
        logger.debug(
            "Native poll by %s: %s (%d options)", ctx.user.username, question, len(options)
        )

    async def _create_button_poll(
        self,
        ctx,
        question: str,
        options: list[str],
        seconds: int | None,
        mode: str,
    ) -> None:
        closes_at: datetime | None = clock.now() + timedelta(seconds=seconds) if seconds else None
        mode_label = "anonyme" if mode == "anonymous" else "classement"
        embed = card(
            "moduleUtils",
            title=f"📊 {question}",
            description=f"*Sondage {mode_label}* — clique sur un bouton pour voter."
            + (f"\nFermeture : {format_discord_timestamp(closes_at, 'R')}" if closes_at else ""),
            footer=poll_footer(_author_name(ctx)),
            footer_icon=ctx.user.avatar_url,
        )
        # Placeholder send so we have a message ID to embed in custom_ids.
        message = await ctx.send(embed=embed)

        poll = Poll(
            channel_id=str(message.channel.id),
            message_id=str(message.id),
            author_id=str(ctx.user.id),
            question=question,
            options=options,
            mode=mode,  # type: ignore[arg-type]
            closes_at=closes_at,
        )
        poll.id = await self._poll_repo(guild_id_of(ctx)).add(poll)

        name, value = render_results_field(poll)
        embed.add_field(name=name, value=value, inline=False)
        await edit_message_if_changed(
            message, embed=embed, components=vote_components(poll), logger=logger
        )

    @modal_callback(EDIT_FORM_RE)
    async def on_edit_form(
        self, ctx: ModalContext, question: str = "", options: str | None = None
    ) -> None:
        """The author's "✏️ Modifier" form: the new question, and the options if nobody voted."""
        match = EDIT_FORM_RE.match(ctx.custom_id)
        if not match:
            return
        repo = self._poll_repo(guild_id_of(ctx))
        new_options = None
        if options is not None:
            try:
                new_options = parse_options(options, limit=BUTTON_OPTION_LIMIT)
            except ValueError as e:
                await send_error(ctx, str(e))
                return
        question = question.strip()[:QUESTION_LIMIT]
        if not question:
            await send_error(ctx, "Écris la question du sondage.")
            return
        edited = await repo.edit(match[1], str(ctx.author.id), question, new_options)
        if edited is None and new_options is not None:
            await send_error(
                ctx,
                "Quelqu'un vient de voter : les options ne changent plus. "
                "Rouvre « Modifier » pour changer la question.",
            )
            return
        if edited is None:
            await send_error(ctx, "Ce sondage est fermé.")
            return
        await self._show_edit(guild_id_of(ctx), edited)
        await ctx.send(ok("Sondage modifié."), ephemeral=True)

    async def _show_edit(self, guild_id, poll: Poll) -> None:
        channel = await fetch_guild_channel(self.bot, guild_id, poll.channel_id)
        try:
            message = await channel.fetch_message(int(poll.message_id)) if channel else None
        except Exception as e:
            logger.warning("Edited poll %s: its message can't be read: %s", poll.id, e)
            return
        if message is None or not message.embeds:
            return
        embed = copy_embed(message.embeds[0])
        prefix = "🔒 📊 " if poll.closed else "📊 "
        embed.title = f"{prefix}{poll.question}"
        update_poll_embed(embed, poll)
        await edit_message_if_changed(
            message, embed=embed, components=vote_components(poll), logger=logger
        )

    @Task.create(IntervalTrigger(seconds=30))
    async def check_closing_polls(self) -> None:
        now = clock.now()
        for gid in UTILS.guild_ids():
            with log_context.scope(guild_id=gid):
                try:
                    due = await self._poll_repo(gid).list_due(now)
                except Exception as e:
                    log_failure(logger, "Failed to fetch due polls for guild %s: %s", gid, e)
                    continue
                for poll in due:
                    if poll.id is None:
                        continue
                    try:  # one poll failing to close doesn't hold back the others
                        await self._close_button_poll(gid, poll)
                    except Exception as e:
                        log_failure(logger, "Could not close poll %s: %s", poll.id, e)

    async def _fetch_due_poll(self, guild_id: str, poll: Poll):
        """The message of a poll due to close, ``None`` when it's gone, or :data:`RETRY`.

        Only a deleted message or channel (or one the bot may no longer read)
        closes the poll without its results. Discord not answering (5xx, a
        timeout) leaves it due, and the next sweep tries again, up to
        :data:`MAX_CLOSE_FAILURES` times, as giveaways do.
        """
        key = str(poll.id)
        try:
            channel = await fetch_guild_channel(
                self.bot, guild_id, poll.channel_id, raise_outages=True
            )
            message = None
            if channel is not None and hasattr(channel, "fetch_message"):
                message = await channel.fetch_message(int(poll.message_id))
        except (NotFound, Forbidden) as e:
            message = None
            logger.warning("Poll %s can't be read any more: %s", poll.id, e)
        except Exception as e:
            failures = self._close_failures.get(key, 0) + 1
            if failures < MAX_CLOSE_FAILURES:
                self._close_failures[key] = failures
                logger.warning("Could not read poll %s, retrying later: %s", poll.id, e)
                return RETRY
            log_failure(
                logger,
                "Poll %s closed without its results: Discord didn't answer %d times (%s)",
                poll.id,
                failures,
                e,
            )
            message = None
        self._close_failures.pop(key, None)
        if message is None:
            logger.warning("Poll %s is gone from channel %s, closing it", poll.id, poll.channel_id)
        return message

    async def _close_button_poll(self, guild_id: str, poll: Poll) -> None:
        poll_id = poll.id
        if poll_id is None:  # read back from the database: it has its id
            return
        message = await self._fetch_due_poll(guild_id, poll)
        if message is RETRY:
            return
        if message is None:
            await self._poll_repo(guild_id).mark_closed(poll_id)
            return

        poll.closed = True
        embed = (
            copy_embed(message.embeds[0])
            if message.embeds
            else card("moduleUtils", title=f"📊 {poll.question}")
        )
        if not (embed.title or "").startswith("🔒"):
            embed.title = f"🔒 {embed.title or poll.question}"
        update_poll_embed(embed, poll)
        try:
            await edit_message_if_changed(
                message, embed=embed, components=vote_components(poll), logger=logger
            )
        except Exception as e:
            logger.warning("Could not edit closed poll %s: %s", poll.id, e)
        await self._poll_repo(guild_id).mark_closed(poll_id)


def setup(bot: Client) -> None:
    PollsExtension(bot)


__all__ = ["PollsExtension", "setup"]
