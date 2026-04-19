"""PollsMixin — poll creation, editing, and reaction-tracking handlers."""

import asyncio

from interactions import (
    Embed,
    IntegrationType,
    OptionType,
    SlashContext,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import (
    MessageReactionAdd,
    MessageReactionRemove,
)

from src.discord_ext.embeds import Colors
from src.discord_ext.messages import send_error
from src.discord_ext.paginator import format_poll

from ._common import (
    DEFAULT_POLL_EMOJIS,
    DEFAULT_POLL_OPTIONS,
    POLL_EMOJIS,
    enabled_servers_int,
    logger,
)


class PollsMixin:
    # asyncio.Lock is initialised in UtilExtension.__init__
    lock: asyncio.Lock

    @staticmethod
    def validate_poll_options(options: list[str]) -> bool:
        """Validate poll options count."""
        return len(options) <= 10

    @staticmethod
    def is_poll_embed(embed: Embed) -> bool:
        """Check if an embed is a poll embed."""
        return embed.color == Colors.UTIL

    @staticmethod
    async def add_poll_reactions(message, options: list[str], use_default: bool = False):
        """Add reactions to a poll message."""
        emojis = DEFAULT_POLL_EMOJIS if use_default else POLL_EMOJIS
        for i in range(len(options)):
            await message.add_reaction(emojis[i])

    @staticmethod
    def parse_poll_author_id(footer_text: str) -> str | None:
        """Extract author ID from poll footer text."""
        if not footer_text or len(footer_text.split(" ")) < 5:
            return None
        return footer_text.split(" ")[4].rstrip(")")

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
    async def poll(self, ctx: SlashContext, question, options=None):
        """
        A slash command that creates a poll.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        question : str
            The question to ask in the poll.
        options : str, optional
            The options for the poll, separated by semicolon. Default is ["Oui", "Non"].
        """
        if options is None:
            options = DEFAULT_POLL_OPTIONS
            emojis = DEFAULT_POLL_EMOJIS
        else:
            options = [option.strip() for option in options.split(";")]
            if not self.validate_poll_options(options):
                await send_error(ctx, "Vous ne pouvez pas créer un sondage avec plus de 10 options")
                return
            emojis = POLL_EMOJIS
        embed = Embed(
            title=question,
            description="\n\n".join([f"{emojis[i]} {option}" for i, option in enumerate(options)]),
            color=Colors.UTIL,
        )
        embed.set_footer(
            text=f"Créé par {ctx.user.username} (ID: {ctx.user.id})",
            icon_url=ctx.user.avatar_url,
        )
        message = await ctx.send(embed=embed)
        await self.add_poll_reactions(
            message, options, use_default=(options == DEFAULT_POLL_OPTIONS)
        )
        logger.debug(
            "Création d'un sondage par %s (ID: %s)\nQuestion : %s\nOptions : %s",
            ctx.user.username,
            ctx.user.id,
            question,
            options,
        )

    @listen(MessageReactionAdd)
    async def on_message_reaction_add(self, event: MessageReactionAdd):
        """
        Count reactions and update the poll embed
        """
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
            # Check if the message is a poll
            if event.message.embeds[0].color == Colors.UTIL:
                # Create the poll embed
                embed = await format_poll(event)
                await event.message.edit(embed=embed)

    @listen(MessageReactionRemove)
    async def on_message_reaction_remove(self, event: MessageReactionRemove):
        """
        Count reactions and update the poll embed
        """
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
            # Check if the message is a poll
            if event.message.embeds[0].color == Colors.UTIL:
                # Create the poll embed
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
        """
        A slash command that edits a poll.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        message_id : str
            The ID of the message to edit.
        question : str, optional
            The new question to ask in the poll.
        options : str, optional
            The new options for the poll, separated by commas.
        reset_reactions : bool, optional
            Whether to reset the reactions of the poll. Default is False.
        """
        await ctx.defer(ephemeral=True)
        try:
            message = await ctx.channel.fetch_message(message_id)
        except Exception:
            await send_error(ctx, "Message introuvable ou inaccessible")
            return

        # At this point, message is guaranteed to be not None
        assert message is not None

        if message.author != ctx.bot.user:
            await send_error(ctx, "Vous ne pouvez modifier que les sondages créés par le bot")
            return
        if not message.embeds:
            await send_error(ctx, "Vous ne pouvez modifier que les sondages créés par le bot")
            return
        if not self.is_poll_embed(message.embeds[0]):
            await send_error(ctx, "Vous ne pouvez modifier que les sondages créés par le bot")
            return
        # Verify if the author of the poll is the person who made the poll
        footer_text = message.embeds[0].footer.text if message.embeds[0].footer else ""
        author_id = self.parse_poll_author_id(footer_text)
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
            if not self.validate_poll_options(options):
                await send_error(ctx, "Vous ne pouvez pas créer un sondage avec plus de 10 options")
                return
            embed.description = "\n\n".join(
                [f"{POLL_EMOJIS[i]} {option}" for i, option in enumerate(options)]
            )
            await self.add_poll_reactions(message, options)
        elif reset_reactions:
            description = embed.description or ""
            option_count = len(description.split("\n\n")) if description else 2
            for i in range(option_count):
                await message.add_reaction(POLL_EMOJIS[i])

        await message.edit(embed=embed)
        logger.info("Poll edited")
        await ctx.send("Sondage modifié", ephemeral=True)
