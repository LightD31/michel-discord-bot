"""Polls Discord extension — poll creation, editing, and reaction tracking.

Slash commands:
- ``/poll`` — reaction-based poll, up to 10 options, optional auto-close
- ``/poll-anonyme`` — button-based poll where voter identities aren't shown
- ``/poll-classement`` — ranked-choice (instant-runoff) poll
- ``/editpoll`` — edit the question, options, or reset reactions of a poll you created

Listens to :class:`MessageReactionAdd` / :class:`MessageReactionRemove` to keep
reaction-based poll embeds' vote counts fresh. Button-based polls persist their
state in MongoDB (``polls`` collection) and close automatically once their
``closes_at`` deadline elapses. Reaction polls are stored there too
(``mode="reaction"``): the reaction listener re-renders and ``/editpoll`` edits
only a stored poll, and its auto-close survives a restart. A poll posted outside
the enabled servers (user install) is not stored, and closes on an in-memory
timer. Enabled per-guild via ``moduleUtils``.
"""

import asyncio
import contextlib
import os
from datetime import datetime, timedelta

from interactions import (
    Client,
    Embed,
    Extension,
    IntegrationType,
    IntervalTrigger,
    OptionType,
    SlashContext,
    Task,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import MessageReactionAdd, MessageReactionRemove

from features.polls import (
    DEFAULT_POLL_EMOJIS,
    DEFAULT_POLL_OPTIONS,
    POLL_EMOJIS,
    Poll,
    PollRepository,
    is_reaction_poll_text,
    options_description,
    parse_duration,
    poll_option_emojis,
    validate_poll_options,
)
from src.core import clock
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.debounce import Coalescer
from src.core.logging import log_failure
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.messages import copy_embed, edit_message_if_changed, send_error
from src.discord_ext.paginator import format_poll
from src.discord_ext.scopes import guild_scopes

from .buttons import PollButtonsMixin, render_results_field, update_poll_embed, vote_components

logger = logutil.init_logger(__name__)
UTILS = ModuleConfig("moduleUtils")
# Votes on a reaction poll within this many seconds share one re-read of its voters.
REFRESH_DELAY_SECONDS = 3


def _default_poll_seconds(guild_id: str | int | None) -> int | None:
    """Per-guild default auto-close (seconds) when ``duree`` is omitted; None disables it."""
    if guild_id is None:
        return None
    cfg = UTILS.get(guild_id)
    minutes = cfg.get("pollDefaultDurationMinutes")
    try:
        value = int(minutes) if minutes is not None else 0
    except (TypeError, ValueError):
        value = 0
    return value * 60 if value > 0 else None


# In-memory map of (guild_id, message_id) → asyncio.Task that auto-closes a
# reaction poll posted outside the enabled servers (user install), where there
# is no guild database to persist it in. Everything else is swept by
# check_closing_polls.
_reaction_close_tasks: dict[tuple[str, str], asyncio.Task] = {}


async def _lock_reaction_poll(message, options: list[str], emojis: list[str]) -> None:
    """Close a reaction poll: lock its title, drop the deadline note, clear reactions."""
    if not message or not message.embeds:
        return
    embed = copy_embed(message.embeds[0])
    # Re-render the description without the relative-timestamp note.
    embed.description = options_description(options, emojis)
    if not (embed.title or "").startswith("🔒"):
        embed.title = f"🔒 {embed.title}"
    embed.color = Colors.WARNING
    await edit_message_if_changed(message, embed=embed, logger=logger)
    with contextlib.suppress(Exception):
        await message.clear_all_reactions()


def _is_poll_embed(embed: Embed) -> bool:
    """A reaction poll's embed: the poll colour, an author footer and option paragraphs.

    The colour alone is shared with RSS posts, role menus, giveaways and button
    polls, which a reaction must never rewrite.
    """
    footer_text = embed.footer.text if embed.footer else None
    return embed.color == Colors.UTIL and is_reaction_poll_text(footer_text, embed.description)


async def _add_poll_reactions(message, options: list[str], use_default: bool = False) -> None:
    emojis = DEFAULT_POLL_EMOJIS if use_default else POLL_EMOJIS
    for i in range(len(options)):
        await message.add_reaction(emojis[i])


class PollsExtension(Extension, PollButtonsMixin):
    MODULE_KEYS = ("moduleUtils",)

    def __init__(self, bot: Client):
        self.bot = bot
        # A burst of votes on a reaction poll re-reads its voters once.
        self._refreshes = Coalescer(REFRESH_DELAY_SECONDS, name="poll-refresh", log=logger)

    def _poll_repo(self, guild_id: str | int) -> PollRepository:
        return PollRepository(str(guild_id))

    @listen()
    async def on_startup(self) -> None:
        for guild_id in UTILS.guild_ids():
            try:
                await self._poll_repo(guild_id).ensure_indexes()
            except Exception as e:
                log_failure(logger, "Could not init poll indexes for %s: %s", guild_id, e)
        self.check_closing_polls.start()

    @slash_command(
        name="poll",
        description="Créer un sondage",
        scopes=guild_scopes(UTILS.guild_ids()),
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )  # type: ignore
    @slash_option(
        "question",
        "Question du sondage",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "options",
        "Options du sondage, séparées par des point-virgules",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_option(
        "duree",
        "Fermeture automatique après ce délai (ex: 30m, 2h, 1d)",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="duration",
    )
    async def poll(
        self,
        ctx: SlashContext,
        question,
        options=None,
        duration: str | None = None,
    ):
        if options is None:
            options = DEFAULT_POLL_OPTIONS
            emojis = DEFAULT_POLL_EMOJIS
        else:
            options = [option.strip() for option in options.split(";")]
            if not validate_poll_options(options):
                await send_error(ctx, "Vous ne pouvez pas créer un sondage avec plus de 10 options")
                return
            emojis = POLL_EMOJIS
        close_seconds: int | None = None
        if duration:
            close_seconds = parse_duration(duration)
            if close_seconds is None:
                await send_error(
                    ctx, "Format de durée invalide. Utilisez par ex. `30m`, `2h`, `1d`."
                )
                return
        else:
            close_seconds = _default_poll_seconds(ctx.guild_id)
        description = "\n\n".join([f"{emojis[i]} {option}" for i, option in enumerate(options)])
        if close_seconds:
            close_time = clock.now() + timedelta(seconds=close_seconds)
            description += (
                f"\n\n*Fermeture automatique : {format_discord_timestamp(close_time, 'R')}*"
            )
        embed = Embed(title=question, description=description, color=Colors.UTIL)
        embed.set_footer(
            text=f"Créé par {ctx.user.username} (ID: {ctx.user.id})",
            icon_url=ctx.user.avatar_url,
        )
        message = await ctx.send(embed=embed)
        await _add_poll_reactions(message, options, use_default=(options == DEFAULT_POLL_OPTIONS))
        await self._record_reaction_poll(ctx, message, close_seconds, question, options, emojis)
        logger.debug(
            "Création d'un sondage par %s (ID: %s)\nQuestion : %s\nOptions : %s",
            ctx.user.username,
            ctx.user.id,
            question,
            options,
        )

    async def _record_reaction_poll(
        self,
        ctx: SlashContext,
        message,
        seconds: int | None,
        question: str,
        options: list[str],
        emojis: list[str],
    ) -> None:
        """Store the poll where polls are on, and arrange its close after *seconds*.

        A stored poll is what the reaction listener re-renders and /editpoll
        edits, and check_closing_polls closes it, even across restarts.
        Elsewhere (user install), or if the database refuses the write, a poll
        with a deadline closes on an in-memory timer instead.
        """
        guild_id = str(ctx.guild_id) if ctx.guild_id else None
        if UTILS.enabled(guild_id):
            poll = Poll(
                channel_id=str(message.channel.id),
                message_id=str(message.id),
                author_id=str(ctx.user.id),
                question=question,
                options=options,
                emojis=emojis[: len(options)],
                mode="reaction",
                closes_at=clock.now() + timedelta(seconds=seconds) if seconds else None,
            )
            try:
                await self._poll_repo(guild_id).add(poll)
                return
            except Exception as e:
                logger.warning("Could not store reaction poll %s: %s", message.id, e)
        if not seconds:
            return

        async def _close():
            try:
                await asyncio.sleep(seconds)
                fresh = await message.channel.fetch_message(message.id)
                await _lock_reaction_poll(fresh, options, emojis)
            except Exception as e:
                logger.warning("Reaction poll auto-close failed: %s", e)
            finally:
                _reaction_close_tasks.pop((str(guild_id), str(message.id)), None)

        task = asyncio.create_task(_close())
        _reaction_close_tasks[(str(guild_id), str(message.id))] = task

    @slash_command(
        name="poll-anonyme",
        description="Créer un sondage à vote anonyme (boutons)",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "question",
        "Question du sondage",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "options",
        "Options du sondage, séparées par des point-virgules",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "duree",
        "Fermeture automatique (ex: 30m, 2h, 1d)",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="duration",
    )
    async def poll_anonyme(
        self,
        ctx: SlashContext,
        question: str,
        options: str,
        duration: str | None = None,
    ):
        await self._create_button_poll(ctx, question, options, duration, mode="anonymous")

    @slash_command(
        name="poll-classement",
        description="Créer un sondage à vote alternatif (ranked-choice)",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "question",
        "Question du sondage",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "options",
        "Options du sondage, séparées par des point-virgules",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "duree",
        "Fermeture automatique (ex: 30m, 2h, 1d)",
        opt_type=OptionType.STRING,
        required=False,
        argument_name="duration",
    )
    async def poll_classement(
        self,
        ctx: SlashContext,
        question: str,
        options: str,
        duration: str | None = None,
    ):
        await self._create_button_poll(ctx, question, options, duration, mode="ranked")

    async def _create_button_poll(
        self,
        ctx: SlashContext,
        question: str,
        options_text: str,
        duration: str | None,
        mode: str,
    ) -> None:
        if not ctx.guild_id:
            await send_error(ctx, "Cette commande est réservée aux serveurs.")
            return
        options = [opt.strip() for opt in options_text.split(";") if opt.strip()]
        if len(options) < 2 or not validate_poll_options(options):
            await send_error(ctx, "Indiquez entre 2 et 10 options séparées par `;`.")
            return

        closes_at: datetime | None = None
        seconds: int | None = None
        if duration:
            seconds = parse_duration(duration)
            if seconds is None:
                await send_error(
                    ctx, "Format de durée invalide. Utilisez par ex. `30m`, `2h`, `1d`."
                )
                return
        else:
            seconds = _default_poll_seconds(ctx.guild_id)
        if seconds:
            closes_at = clock.now() + timedelta(seconds=seconds)

        mode_label = "anonyme" if mode == "anonymous" else "classement"
        embed = Embed(
            title=f"📊 {question}",
            description=f"*Sondage {mode_label}* — cliquez sur un bouton pour voter."
            + (f"\nFermeture : {format_discord_timestamp(closes_at, 'R')}" if closes_at else ""),
            color=Colors.UTIL,
        )
        embed.set_footer(
            text=f"Créé par {ctx.user.username} (ID: {ctx.user.id})",
            icon_url=ctx.user.avatar_url,
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
        poll.id = await self._poll_repo(ctx.guild_id).add(poll)

        name, value = render_results_field(poll)
        embed.add_field(name=name, value=value, inline=False)
        await edit_message_if_changed(
            message, embed=embed, components=vote_components(poll), logger=logger
        )

    @Task.create(IntervalTrigger(seconds=30))
    async def check_closing_polls(self) -> None:
        now = clock.now()
        for gid in UTILS.guild_ids():
            try:
                due = await self._poll_repo(gid).list_due(now)
            except Exception as e:
                log_failure(logger, "Failed to fetch due polls for guild %s: %s", gid, e)
                continue
            for poll in due:
                if poll.id is None:
                    continue
                if poll.mode == "reaction":
                    await self._close_reaction_poll(gid, poll)
                else:
                    await self._close_button_poll(gid, poll)

    async def _close_reaction_poll(self, guild_id: str, poll: Poll) -> None:
        try:
            channel = await self.bot.fetch_channel(int(poll.channel_id))
            message = await channel.fetch_message(int(poll.message_id))
        except Exception as e:
            logger.warning("Could not fetch poll %s in channel %s: %s", poll.id, poll.channel_id, e)
            await self._poll_repo(guild_id).mark_closed(poll.id)
            return
        emojis = poll.emojis or (
            DEFAULT_POLL_EMOJIS if poll.options == DEFAULT_POLL_OPTIONS else POLL_EMOJIS
        )
        try:
            await _lock_reaction_poll(message, poll.options, emojis)
        except Exception as e:
            logger.warning("Could not close reaction poll %s: %s", poll.id, e)
        await self._poll_repo(guild_id).mark_closed(poll.id)

    async def _close_button_poll(self, guild_id: str, poll: Poll) -> None:
        try:
            channel = await self.bot.fetch_channel(int(poll.channel_id))
            message = await channel.fetch_message(int(poll.message_id))
        except Exception as e:
            logger.warning("Could not fetch poll %s in channel %s: %s", poll.id, poll.channel_id, e)
            await self._poll_repo(guild_id).mark_closed(poll.id)
            return

        poll.closed = True
        embed = (
            copy_embed(message.embeds[0]) if message.embeds else Embed(title=f"📊 {poll.question}")
        )
        if not embed.title.startswith("🔒"):
            embed.title = f"🔒 {embed.title}"
        update_poll_embed(embed, poll)
        try:
            await edit_message_if_changed(
                message, embed=embed, components=vote_components(poll), logger=logger
            )
        except Exception as e:
            logger.warning("Could not edit closed poll %s: %s", poll.id, e)
        await self._poll_repo(guild_id).mark_closed(poll.id)

    def _reaction_poll_message(self, event: MessageReactionAdd | MessageReactionRemove):
        """The reaction poll *event* happened on, or None for any other message.

        Checked before any work: a reaction poll is the bot's own message, in a
        server where polls are on, and the reaction is one of its options.
        """
        message = event.message
        guild = getattr(message, "guild", None)
        if guild is None or not UTILS.enabled(guild.id):
            return None
        if not message.embeds or message.author.id != self.bot.user.id:
            return None
        embed = message.embeds[0]
        if not _is_poll_embed(embed):
            return None
        if str(event.emoji) not in poll_option_emojis(embed.description):
            return None
        return message

    def _refresh_reaction_poll(self, event: MessageReactionAdd | MessageReactionRemove) -> None:
        """Schedule a re-render of the reaction poll *event* voted on, if it is one."""
        message = self._reaction_poll_message(event)
        if message is None:
            return
        self._refreshes.request(int(message.id), lambda: self._render_reaction_poll(message))

    async def _render_reaction_poll(self, message) -> None:
        if await self._stored_reaction_poll(message.guild.id, message.id) is None:
            return
        embed = await format_poll(message)
        await edit_message_if_changed(message, embed=embed, logger=logger)

    @listen(MessageReactionAdd)
    async def on_message_reaction_add(self, event: MessageReactionAdd):
        """Count reactions and update the poll embed."""
        self._refresh_reaction_poll(event)

    @listen(MessageReactionRemove)
    async def on_message_reaction_remove(self, event: MessageReactionRemove):
        """Count reactions and update the poll embed."""
        self._refresh_reaction_poll(event)

    @slash_command(
        name="editpoll",
        description="Modifier un sondage",
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        "message_id",
        "ID du message à modifier",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "question",
        "Question du sondage",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_option(
        "options",
        "Options du sondage, séparées par des point-virgules",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_option(
        "reset_reactions",
        "Réinitialiser les réactions du sondage",
        opt_type=OptionType.BOOLEAN,
        required=False,
    )
    async def editpoll(
        self,
        ctx: SlashContext,
        message_id,
        question=None,
        options=None,
        reset_reactions=False,
    ):
        await ctx.defer(ephemeral=True)
        try:
            message = await ctx.channel.fetch_message(message_id)
        except Exception:
            await send_error(ctx, "Message introuvable ou inaccessible")
            return

        assert message is not None

        stored = await self._stored_reaction_poll(ctx.guild_id, message.id)
        if stored is None or not message.embeds:
            await send_error(ctx, "Ce message n'est pas un sondage à réactions de ce serveur")
            return
        if stored.author_id != str(ctx.user.id):
            await send_error(ctx, "Vous ne pouvez modifier que les sondages que vous avez créés")
            return
        embed = copy_embed(message.embeds[0])
        if reset_reactions:
            await message.clear_all_reactions()
        if question is not None:
            embed.title = f"{question} (modifié)"
        else:
            embed.title = f"{embed.title} (modifié)"
        if options is not None:
            options = [option.strip() for option in options.split(";")]
            if not validate_poll_options(options):
                await send_error(ctx, "Vous ne pouvez pas créer un sondage avec plus de 10 options")
                return
            embed.description = "\n\n".join(
                [f"{POLL_EMOJIS[i]} {option}" for i, option in enumerate(options)]
            )
            await _add_poll_reactions(message, options)
            await self._update_stored_options(ctx.guild_id, stored, options)
        elif reset_reactions:
            description = embed.description or ""
            option_count = len(description.split("\n\n")) if description else 2
            for i in range(option_count):
                await message.add_reaction(POLL_EMOJIS[i])

        await edit_message_if_changed(message, embed=embed, logger=logger)
        logger.info("Poll edited")
        await ctx.send("Sondage modifié", ephemeral=True)

    async def _stored_reaction_poll(self, guild_id, message_id) -> Poll | None:
        """The reaction poll stored for *message_id*, or None for any other message."""
        try:
            stored = await self._poll_repo(guild_id).get_by_message(str(message_id))
        except Exception as e:
            logger.warning("Could not read poll %s: %s", message_id, e)
            return None
        return stored if stored is not None and stored.mode == "reaction" else None

    async def _update_stored_options(self, guild_id, stored: Poll, options: list[str]) -> None:
        """Keep a stored reaction poll's options in step with /editpoll, for its close."""
        if stored.id is None or stored.closed:
            return
        try:
            await self._poll_repo(guild_id).set_options(
                stored.id, options, POLL_EMOJIS[: len(options)]
            )
        except Exception as e:
            logger.warning("Could not update stored poll %s: %s", stored.message_id, e)


def setup(bot: Client) -> None:
    PollsExtension(bot)


__all__ = ["PollsExtension", "setup"]
