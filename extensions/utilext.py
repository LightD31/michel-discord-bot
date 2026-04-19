"""
Utility Extension for Discord Bot

This module provides various utility commands including:
- Ping command for latency checking
- Message deletion with admin permissions
- Message sending to channels
- Poll creation and management with reaction tracking
- Reminder system with scheduling and frequency options

Recent improvements:
- Fixed type safety issues with proper type hints
- Added error handling for file operations and API calls
- Refactored poll functionality with helper methods
- Reduced code duplication with constants and utility functions
- Improved null safety with proper optional handling
"""

import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional

from dateutil.relativedelta import relativedelta
from interactions import (
    ActionRow,
    BaseChannel,
    Button,
    ButtonStyle,
    ChannelType,
    Client,
    Embed,
    Extension,
    IntegrationType,
    IntervalTrigger,
    OptionType,
    Permissions,
    SlashCommandChoice,
    SlashContext,
    Task,
    TimestampStyles,
    listen,
    slash_command,
    slash_default_member_permission,
    slash_option,
)
from interactions.api.events import (
    MessageReactionAdd,
    MessageReactionRemove,
)
from interactions.client.utils import timestamp_converter

from features.reminders import Reminder, ReminderRepository
from src import logutil
from src.config_manager import load_config
from src.helpers import Colors, fetch_user_safe, is_guild_enabled, send_error
from src.utils import format_poll
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleUtils")
class UtilsConfig(SchemaBase):
    __label__ = "Utilitaires"
    __description__ = "Commandes utilitaires : ping, sondages, rappels, suppression de messages."
    __icon__ = "🛠️"
    __category__ = "Outils"

    enabled: bool = enabled_field()


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleUtils")
# Convert strings to integers for Discord snowflake IDs
# Type ignore because Discord IDs are ints but type checker expects Snowflake_Type
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore

# Poll emojis constant
POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# Default poll options
DEFAULT_POLL_OPTIONS = ["Oui", "Non"]
DEFAULT_POLL_EMOJIS = ["👍", "👎"]


class UtilExtension(Extension):
    def __init__(self, bot: Client):
        self.bot = bot
        self.lock = asyncio.Lock()
        self._reminder_repos: dict[str, ReminderRepository] = {}

    def _reminder_repo(self, guild_id: str | int) -> ReminderRepository:
        gid = str(guild_id)
        repo = self._reminder_repos.get(gid)
        if repo is None:
            repo = ReminderRepository(gid)
            self._reminder_repos[gid] = repo
        return repo

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

    @listen()
    async def on_startup(self):
        for guild_id in enabled_servers:
            await self._reminder_repo(guild_id).ensure_indexes()
        self.check_reminders.start()

    @slash_command(
        name="ping",
        description="Vérifier la latence du bot",
        scopes=enabled_servers_int,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],  # type: ignore
    )
    async def ping(self, ctx: SlashContext):
        """
        A slash command that checks the latency of the bot.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        """
        await ctx.send(f"Pong ! Latence : {round(ctx.bot.latency * 1000)}ms")

    @slash_command(
        name="delete",
        description="Supprimer des messages",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "nombre",
        "Nombre de messages à supprimer",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
    )
    @slash_option(
        "channel",
        "Channel dans lequel supprimer les messages",
        opt_type=OptionType.CHANNEL,
        required=False,
    )
    @slash_option(
        "before",
        "Supprimer les messages avant le message avec cet ID",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_option(
        "after",
        "Supprimer les messages après le message avec cet ID",
        opt_type=OptionType.STRING,
        required=False,
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_MESSAGES)
    async def delete(
        self,
        ctx: SlashContext,
        nombre=1,
        channel=None,
        before=None,
        after=None,
    ):
        """
        A slash command that deletes messages in a channel.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        nombre : int, optional
            The number of messages to delete. Default is 1.
        channel : discord.TextChannel, optional
            The channel in which to delete messages. Default is the current channel.
        before : int, optional
            Delete messages before this message ID.
        after : int, optional
            Delete messages after this message ID.
        """
        if channel is None:
            channel = ctx.channel
        await channel.purge(
            deletion_limit=nombre,
            reason=f"Suppression de {nombre} message(s) par {ctx.user.username} (ID: {ctx.user.id}) via la commande /delete",
            before=before,
            after=after,
        )
        await ctx.send(
            f"{nombre} message(s) supprimé(s) dans le channel <#{channel.id}>",
            ephemeral=True,
        )
        logger.info(
            "Suppression de %s message(s) par %s (ID: %s) via la commande /delete",
            nombre,
            ctx.user.username,
            ctx.user.id,
        )

    @slash_command(
        name="send",
        description="Envoyer un message dans un channel",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        "message",
        "Message à envoyer",
        opt_type=OptionType.STRING,
        required=True,
    )
    @slash_option(
        "channel",
        "Channel dans lequel envoyer le message",
        opt_type=OptionType.CHANNEL,
        required=False,
        channel_types=[
            ChannelType.GUILD_TEXT,
            ChannelType.GUILD_NEWS,
        ],
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR | Permissions.MANAGE_MESSAGES)
    async def send(
        self,
        ctx: SlashContext,
        message: str,
        channel: BaseChannel | None = None,
    ):
        """
        A slash command that sends a message to a channel.

        Parameters:
        -----------
        ctx : SlashContext
            The context of the slash command.
        message : str
            The message to send.
        """

        if channel is None:
            channel = ctx.channel
        # Check if the channel is a category
        if channel.type == ChannelType.GUILD_CATEGORY:
            await send_error(ctx, "Vous ne pouvez pas envoyer de message dans une catégorie")
            return

        # Ensure channel is a text channel that can send messages
        if not hasattr(channel, "send"):
            await send_error(ctx, "Ce type de channel ne supporte pas l'envoi de messages")
            return

        # Type cast to ensure the channel has the send method
        from typing import cast

        from interactions import GuildText

        text_channel = cast(GuildText, channel)
        sent = await text_channel.send(message)
        logger.info(
            "%s (ID: %s) a envoyé un message dans le channel #%s (ID: %s)",
            ctx.user.username,
            ctx.user.id,
            sent.channel.name,
            sent.channel.id,
        )
        await ctx.send("Message envoyé !", ephemeral=True)

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

    # @listen()
    # async def on_message(self, event: MessageCreate):
    #     """
    #     This method is called when a message is received.

    #     Args:
    #         event (interactions.api.events.MessageCreate): The message event.
    #     """
    #     if (
    #         event.message.channel.type == ChannelType.DM
    #         or event.message.channel.type == ChannelType.GROUP_DM
    #     ) and event.message.author.id != self.bot.user.id:
    #         logger.info(
    #             "Message from %s (ID: %s) in DMs : %s",
    #             event.message.author.username,
    #             event.message.author.id,
    #             event.message.content,
    #         )

    # Set reminder
    @slash_command(
        name="reminder",
        sub_cmd_name="set",
        description="Gère les rappels pour voter",
        sub_cmd_description="Ajoute un rappel",
        scopes=enabled_servers_int,  # type: ignore
    )
    @slash_option(
        name="tache",
        description="Tâche à rappeler",
        opt_type=OptionType.STRING,
        required=True,
        argument_name="task",
    )
    @slash_option(
        name="heure",
        description="Heure du rappel",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=23,
        argument_name="hour",
    )
    @slash_option(
        "minute",
        "Minute du rappel",
        OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=59,
    )
    @slash_option(
        "périodicité",
        "Définit la périodicité du rappel (Défaut : Aucune)",
        OptionType.STRING,
        choices=[
            SlashCommandChoice(name="Quotidien", value="daily"),
            SlashCommandChoice(name="Hebdomadaire", value="weekly"),
            SlashCommandChoice(name="Mensuel", value="monthly"),
            SlashCommandChoice(name="Annuel", value="yearly"),
        ],
        required=False,
        argument_name="frequency",
    )
    @slash_option(
        "date",
        "JJ/MM/AAAA. Date de début du rappel (Défaut : aujoud'hui)",
        OptionType.STRING,
        required=False,
    )
    async def reminder_set(
        self,
        ctx: SlashContext,
        hour: int,
        minute: int,
        task: str,
        frequency: str | None = None,
        date: str | None = None,
    ):
        # Create the reminder time from the provided date, hour, and minute
        current_time = datetime.now()
        remind_time = current_time.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if date:
            try:
                parseddate = datetime.strptime(date, "%d/%m/%Y")
            except ValueError:
                await send_error(ctx, "Format de date invalide. Utilisez JJ/MM/AAAA")
                return
            remind_time = remind_time.replace(
                year=parseddate.year, month=parseddate.month, day=parseddate.day
            )

        # If the remind_time is in the past, roll it forward according to frequency.
        while remind_time <= current_time:
            if frequency is None:
                remind_time += timedelta(days=1)
                if remind_time <= current_time:
                    await ctx.send("Le rappel ne peut pas être dans le passé")
                break
            elif frequency == "daily":
                remind_time += timedelta(days=1)
            elif frequency == "weekly":
                remind_time += timedelta(weeks=1)
            elif frequency == "monthly":
                remind_time += relativedelta(months=1)
            elif frequency == "yearly":
                remind_time += relativedelta(years=1)

        gid = str(ctx.guild_id)
        reminder = Reminder(
            user_id=str(ctx.author.id),
            message=task,
            remind_time=remind_time,
            frequency=frequency,  # type: ignore[arg-type]
        )
        await self._reminder_repo(gid).add(reminder)

        await ctx.send(
            f"Rappel {frequency} créé à {remind_time.strftime('%H:%M')} avec le message: {task}",
            ephemeral=True,
        )
        logger.info(
            "Reminder set for %s at %s with message %s (%s)",
            ctx.author.display_name,
            remind_time.strftime("%H:%M"),
            task,
            frequency,
        )

    @reminder_set.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Supprime un rappel",
    )
    async def delete_reminder(self, ctx):
        user_id = str(ctx.author.id)
        gid = str(ctx.guild_id)
        repo = self._reminder_repo(gid)
        reminders = await repo.list_for_user(user_id)

        if not reminders:
            await send_error(ctx, "Tu n'as aucun rappel")
            return

        id_map: dict[str, Reminder] = {}
        buttons = []
        for reminder in reminders:
            if reminder.id is None:
                continue
            remind_time = reminder.remind_time
            if reminder.frequency == "daily":
                label = f"{reminder.message:40} (Tous les jours à {remind_time.strftime('%H:%M')})"
            elif reminder.frequency == "weekly":
                label = (
                    f"{reminder.message:40} (Tous les {remind_time.strftime('%A')} "
                    f"à {remind_time.strftime('%H:%M')})"
                )
            elif reminder.frequency == "monthly":
                label = (
                    f"{reminder.message:40} (Tous les {remind_time.strftime('%d')} "
                    f"à {remind_time.strftime('%H:%M')})"
                )
            elif reminder.frequency == "yearly":
                label = (
                    f"{reminder.message:40} (Tous les {remind_time.strftime('%d/%m')} "
                    f"à {remind_time.strftime('%H:%M')})"
                )
            else:
                label = f"{reminder.message:40} ({remind_time.strftime('%H:%M')})"
            buttons.append(
                Button(label=label, style=ButtonStyle.SECONDARY, custom_id=reminder.id)
            )
            id_map[reminder.id] = reminder

        message = await ctx.send(
            f"Quel rappel veux-tu supprimer (annulation {timestamp_converter(datetime.now() + timedelta(seconds=60)).format(TimestampStyles.RelativeTime)})",
            components=[ActionRow(*buttons)],
            ephemeral=True,
        )

        try:
            button_ctx = await self.bot.wait_for_component(
                components=[button.custom_id for button in buttons], timeout=60
            )
            ctx = button_ctx.ctx
            selected = id_map.get(ctx.custom_id)

            if selected and selected.id:
                deleted = await repo.delete(selected.id)
                if deleted:
                    await ctx.edit_origin(
                        content=f"Rappel à {selected.remind_time.strftime('%H:%M')} supprimé.",
                        components=[],
                    )
                    logger.info(
                        "Reminder at %s deleted for %s",
                        selected.remind_time.strftime("%H:%M"),
                        ctx.author.display_name,
                    )
                else:
                    await send_error(ctx, "Rappel introuvable")
            else:
                await send_error(ctx, "Rappel introuvable")
        except TimeoutError:
            await ctx.edit(
                message=message, content="Annulé, aucun rappel sélectionné", components=[]
            )

    @staticmethod
    def _next_occurrence(remind_time: datetime, frequency: str | None) -> datetime | None:
        if frequency == "daily":
            return remind_time + timedelta(days=1)
        if frequency == "weekly":
            return remind_time + timedelta(weeks=1)
        if frequency == "monthly":
            return remind_time + relativedelta(months=1)
        if frequency == "yearly":
            return remind_time + relativedelta(years=1)
        return None

    @Task.create(IntervalTrigger(minutes=1))
    async def check_reminders(self):
        now = datetime.now()
        for gid in enabled_servers:
            repo = self._reminder_repo(gid)
            try:
                due = await repo.list_due(now)
            except Exception as e:
                logger.error("Failed to fetch due reminders for guild %s: %s", gid, e)
                continue

            for reminder in due:
                if reminder.id is None:
                    continue
                try:
                    _, user = await fetch_user_safe(self.bot, reminder.user_id)
                    if user:
                        await user.send(reminder.message)
                        logger.info(
                            "Reminder sent to %s: %s", user.display_name, reminder.message
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to send reminder to user %s: %s", reminder.user_id, e
                    )
                    continue

                next_time = self._next_occurrence(reminder.remind_time, reminder.frequency)
                try:
                    if next_time is not None:
                        # Roll forward past any missed occurrences so recurring
                        # reminders don't fire repeatedly after downtime.
                        while next_time <= now:
                            next_time = self._next_occurrence(next_time, reminder.frequency)
                            if next_time is None:
                                break
                    if next_time is not None:
                        await repo.reschedule(reminder.id, next_time)
                    else:
                        await repo.delete(reminder.id)
                except Exception as e:
                    logger.error(
                        "Failed to update reminder %s in guild %s: %s", reminder.id, gid, e
                    )
