"""Polls Discord extension — poll creation, editing, and reaction tracking.

Slash commands:
- ``/poll`` — reaction-based poll, up to 10 options, optional auto-close
- ``/poll-anonyme`` — button-based poll where voter identities aren't shown
- ``/poll-classement`` — ranked-choice (instant-runoff) poll
- ``/editpoll`` — edit the question, options, or reset reactions of a poll you created

Listens to :class:`MessageReactionAdd` / :class:`MessageReactionRemove` to keep
reaction-based poll embeds' vote counts fresh. Button-based polls persist their
state in MongoDB (``polls`` collection) and close automatically once their
``closes_at`` deadline elapses. Enabled per-guild via ``moduleUtils``.
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
    parse_duration,
    parse_poll_author_id,
    validate_poll_options,
)
from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.messages import send_error
from src.discord_ext.paginator import format_poll

from .buttons import PollButtonsMixin, render_results_field, update_poll_embed, vote_components

logger = logutil.init_logger(os.path.basename(__file__))
_, _, enabled_servers = load_config("moduleUtils")
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore[misc]

# In-memory map of (guild_id, message_id) → asyncio.Task that auto-closes
# reaction-based polls. Button polls use the periodic task below instead.
_reaction_close_tasks: dict[tuple[str, str], asyncio.Task] = {}


def _is_poll_embed(embed: Embed) -> bool:
    return embed.color == Colors.UTIL


async def _add_poll_reactions(message, options: list[str], use_default: bool = False) -> None:
    emojis = DEFAULT_POLL_EMOJIS if use_default else POLL_EMOJIS
    for i in range(len(options)):
        await message.add_reaction(emojis[i])


class PollsExtension(Extension, PollButtonsMixin):
    def __init__(self, bot: Client):
        self.bot = bot
        self.lock = asyncio.Lock()
        self._poll_repos: dict[str, PollRepository] = {}

    def _poll_repo(self, guild_id: str | int) -> PollRepository:
        gid = str(guild_id)
        repo = self._poll_repos.get(gid)
        if repo is None:
            repo = PollRepository(gid)
            self._poll_repos[gid] = repo
        return repo

    @listen()
    async def on_startup(self) -> None:
        for guild_id in enabled_servers:
            try:
                await self._poll_repo(guild_id).ensure_indexes()
            except Exception as e:
                logger.error("Could not init poll indexes for %s: %s", guild_id, e)
        self.check_closing_polls.start()

    @slash_command(
        name="poll",
        description="Créer un sondage",
        scopes=enabled_servers_int,
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
        description = "\n\n".join([f"{emojis[i]} {option}" for i, option in enumerate(options)])
        if close_seconds:
            close_time = datetime.now() + timedelta(seconds=close_seconds)
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
        if close_seconds:
            self._schedule_reaction_close(
                ctx.guild_id, message, close_seconds, options, emojis
            )
        logger.debug(
            "Création d'un sondage par %s (ID: %s)\nQuestion : %s\nOptions : %s",
            ctx.user.username,
            ctx.user.id,
            question,
            options,
        )

    def _schedule_reaction_close(
        self,
        guild_id,
        message,
        seconds: int,
        options: list[str],
        emojis: list[str],
    ) -> None:
        async def _close():
            try:
                await asyncio.sleep(seconds)
                fresh = await message.channel.fetch_message(message.id)
                if not fresh or not fresh.embeds:
                    return
                embed = fresh.embeds[0]
                # Re-render the description without the relative-timestamp note.
                embed.description = "\n\n".join(
                    f"{emojis[i]} {option}" for i, option in enumerate(options)
                )
                embed.title = f"🔒 {embed.title}"
                embed.color = Colors.WARNING
                await fresh.edit(embed=embed)
                with contextlib.suppress(Exception):
                    await fresh.clear_all_reactions()
            except Exception as e:
                logger.warning("Reaction poll auto-close failed: %s", e)
            finally:
                _reaction_close_tasks.pop((str(guild_id), str(message.id)), None)

        task = asyncio.create_task(_close())
        _reaction_close_tasks[(str(guild_id), str(message.id))] = task

    @slash_command(
        name="poll-anonyme",
        description="Créer un sondage à vote anonyme (boutons)",
        scopes=enabled_servers_int,  # type: ignore
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
        scopes=enabled_servers_int,  # type: ignore
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
        if duration:
            seconds = parse_duration(duration)
            if seconds is None:
                await send_error(
                    ctx, "Format de durée invalide. Utilisez par ex. `30m`, `2h`, `1d`."
                )
                return
            closes_at = datetime.now() + timedelta(seconds=seconds)

        mode_label = "anonyme" if mode == "anonymous" else "classement"
        embed = Embed(
            title=f"📊 {question}",
            description=f"*Sondage {mode_label}* — cliquez sur un bouton pour voter."
            + (
                f"\nFermeture : {format_discord_timestamp(closes_at, 'R')}"
                if closes_at
                else ""
            ),
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
        await message.edit(embed=embed, components=vote_components(poll))

    @Task.create(IntervalTrigger(seconds=30))
    async def check_closing_polls(self) -> None:
        now = datetime.now()
        for gid in enabled_servers:
            try:
                due = await self._poll_repo(gid).list_due(now)
            except Exception as e:
                logger.error("Failed to fetch due polls for guild %s: %s", gid, e)
                continue
            for poll in due:
                if poll.id is None:
                    continue
                await self._close_button_poll(gid, poll)

    async def _close_button_poll(self, guild_id: str, poll: Poll) -> None:
        try:
            channel = await self.bot.fetch_channel(int(poll.channel_id))
            message = await channel.fetch_message(int(poll.message_id))
        except Exception as e:
            logger.warning(
                "Could not fetch poll %s in channel %s: %s", poll.id, poll.channel_id, e
            )
            await self._poll_repo(guild_id).mark_closed(poll.id)
            return

        poll.closed = True
        embed = message.embeds[0] if message.embeds else Embed(title=f"📊 {poll.question}")
        if not embed.title.startswith("🔒"):
            embed.title = f"🔒 {embed.title}"
        update_poll_embed(embed, poll)
        try:
            await message.edit(embed=embed, components=vote_components(poll))
        except Exception as e:
            logger.warning("Could not edit closed poll %s: %s", poll.id, e)
        await self._poll_repo(guild_id).mark_closed(poll.id)

    @listen(MessageReactionAdd)
    async def on_message_reaction_add(self, event: MessageReactionAdd):
        """Count reactions and update the poll embed."""
        async with self.lock:
            logger.debug(
                "Reaction added : %s\npoll message id : %s\nperson : %s\nreaction : %s",
                event.emoji,
                event.message,
                event.author,
                event.reaction,
            )
            if len(event.message.embeds) == 0:
                return
            if event.message.embeds[0].color == Colors.UTIL:
                embed = await format_poll(event)
                await event.message.edit(embed=embed)

    @listen(MessageReactionRemove)
    async def on_message_reaction_remove(self, event: MessageReactionRemove):
        """Count reactions and update the poll embed."""
        async with self.lock:
            logger.debug(
                "Reaction removed : %s\npoll message id : %s\nperson : %s\nreaction : %s",
                event.emoji,
                event.message,
                event.author,
                event.reaction,
            )
            if len(event.message.embeds) == 0:
                return
            if event.message.embeds[0].color == Colors.UTIL:
                embed = await format_poll(event)
                await event.message.edit(embed=embed)

    @slash_command(
        name="editpoll",
        description="Modifier un sondage",
        scopes=enabled_servers_int,  # type: ignore
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

        if message.author != ctx.bot.user:
            await send_error(ctx, "Vous ne pouvez modifier que les sondages créés par le bot")
            return
        if not message.embeds:
            await send_error(ctx, "Vous ne pouvez modifier que les sondages créés par le bot")
            return
        if not _is_poll_embed(message.embeds[0]):
            await send_error(ctx, "Vous ne pouvez modifier que les sondages créés par le bot")
            return
        footer_text = message.embeds[0].footer.text if message.embeds[0].footer else ""
        author_id = parse_poll_author_id(footer_text)
        if not author_id or author_id != str(ctx.user.id):
            await send_error(
                ctx,
                "Vous ne pouvez modifier que les sondages que vous avez créés"
                if author_id
                else "Impossible de vérifier l'auteur de ce sondage",
            )
            return
        embed = message.embeds[0]
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
        elif reset_reactions:
            description = embed.description or ""
            option_count = len(description.split("\n\n")) if description else 2
            for i in range(option_count):
                await message.add_reaction(POLL_EMOJIS[i])

        await message.edit(embed=embed)
        logger.info("Poll edited")
        await ctx.send("Sondage modifié", ephemeral=True)


def setup(bot: Client) -> None:
    PollsExtension(bot)


__all__ = ["PollsExtension", "setup"]
