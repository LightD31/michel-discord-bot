import asyncio
import os
import uuid
import json
from dateutil.relativedelta import relativedelta
from interactions import (
    TimestampStyles,
    BaseChannel,
    ChannelType,
    Client,
    Embed,
    Extension,
    Message,
    OptionType,
    Permissions,
    SlashContext,
    client,
    listen,
    slash_command,
    slash_default_member_permission,
    slash_option,
    Button,
    Task,
    IntervalTrigger,
    ActionRow,
    ButtonStyle,
    User,
    SlashCommandChoice,
)
from interactions.api.events import (
    MessageCreate,
    MessageReactionAdd,
    MessageReactionRemove,
    Component,
)
from interactions.client.utils import timestamp_converter
from datetime import datetime, timedelta
from src import logutil
from src.utils import format_poll, load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleUtils")

# Keep track of reminders
reminders = {}


class Utils(Extension):
    def __init__(self, bot: client):
        self.bot: Client = bot
        self.lock = asyncio.Lock()

    @listen()
    async def on_startup(self):
        await self.load_reminders()
        self.check_reminders.start()

    @slash_command(
        name="ping", description="Vérifier la latence du bot", scopes=enabled_servers
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
        name="delete", description="Supprimer des messages", scopes=enabled_servers
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
    # @slash_default_member_permission(
    #     Permissions.ADMINISTRATOR | Permissions.MANAGE_MESSAGES
    # )
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
        scopes=enabled_servers,
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
    @slash_default_member_permission(
        Permissions.ADMINISTRATOR | Permissions.MANAGE_MESSAGES
    )
    async def send(
        self,
        ctx: SlashContext,
        message: Message,
        channel: BaseChannel = None,
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
            await ctx.send(
                "Vous ne pouvez pas envoyer de message dans une catégorie",
                ephemeral=True,
            )
            return
        sent = await ctx.channel.send(message)
        logger.info(
            "%s (ID: %s) a envoyé un message dans le channel #%s (ID: %s)",
            ctx.user.username,
            ctx.user.id,
            sent.channel.name,
            sent.channel.id,
        )
        await ctx.send("Message envoyé !", ephemeral=True)

    @slash_command(name="poll", description="Créer un sondage", scopes=enabled_servers)
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
            options = ["Oui", "Non"]
            emojis = ["👍", "👎"]
        else:
            options = [option.strip() for option in options.split(";")]
            if len(options) > 10:
                await ctx.send(
                    "Vous ne pouvez pas créer un sondage avec plus de 10 options",
                    ephemeral=True,
                )
                return
            emojis = [
                "1️⃣",
                "2️⃣",
                "3️⃣",
                "4️⃣",
                "5️⃣",
                "6️⃣",
                "7️⃣",
                "8️⃣",
                "9️⃣",
                "🔟",
            ]
        embed = Embed(
            title=question,
            description="\n\n".join(
                [f"{emojis[i]} {option}" for i, option in enumerate(options)]
            ),
            color=0x3489EB,
        )
        embed.set_footer(
            text=f"Créé par {ctx.user.username} (ID: {ctx.user.id})",
            icon_url=ctx.user.avatar_url,
        )
        message = await ctx.send(embed=embed)
        for i in range(len(options)):
            await message.add_reaction(emojis[i])
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
            if event.message.embeds[0].color == 0x3489EB:
                # Create the poll embed
                embed = await format_poll(event, config)
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
            if event.message.embeds[0].color == 0x3489EB:
                # Create the poll embed
                embed = await format_poll(event, config)
                await event.message.edit(embed=embed)

    @slash_command(
        name="editpoll", description="Modifier un sondage", scopes=enabled_servers
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
        message = await ctx.channel.fetch_message(message_id)
        if message.author != ctx.bot.user:
            await ctx.send(
                "Vous ne pouvez modifier que les sondages créés par le bot",
                ephemeral=True,
            )
            return
        if len(message.embeds) == 0:
            await ctx.send(
                "Vous ne pouvez modifier que les sondages créés par le bot",
                ephemeral=True,
            )
            return
        if message.embeds[0].color != 0x3489EB:
            await ctx.send(
                "Vous ne pouvez modifier que les sondages créés par le bot",
                ephemeral=True,
            )
            return
        # Verify if the author of the poll is the person who made the poll
        if message.embeds[0].footer.text.split(" ")[4][0:-1] != str(ctx.user.id):
            await ctx.send(
                "Vous ne pouvez modifier que les sondages que vous avez créés",
                ephemeral=True,
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
            if len(options) > 10:
                await ctx.send(
                    "Vous ne pouvez pas créer un sondage avec plus de 10 options",
                    ephemeral=True,
                )
                return
            emojis = [
                "1️⃣",
                "2️⃣",
                "3️⃣",
                "4️⃣",
                "5️⃣",
                "6️⃣",
                "7️⃣",
                "8️⃣",
                "9️⃣",
                "🔟",
            ]
            embed.description = "\n\n".join(
                [f"{emojis[i]} {option}" for i, option in enumerate(options)]
            )
            for i in range(len(options)):
                await message.add_reaction(emojis[i])
        elif reset_reactions:
            emojis = [
                "1️⃣",
                "2️⃣",
                "3️⃣",
                "4️⃣",
                "5️⃣",
                "6️⃣",
                "7️⃣",
                "8️⃣",
                "9️⃣",
                "🔟",
            ]
            for i in range(len(embed.description.split("\n\n"))):
                await message.add_reaction(emojis[i])

        await message.edit(embed=embed)
        logger.info("Poll edited")
        await ctx.send("Sondage modifié", ephemeral=True)

    @listen()
    async def on_message(self, event: MessageCreate):
        """
        This method is called when a message is received.

        Args:
            event (interactions.api.events.MessageCreate): The message event.
        """
        if (
            event.message.channel.type == ChannelType.DM
            or event.message.channel.type == ChannelType.GROUP_DM
        ) and event.message.author.id != self.bot.user.id:
            logger.info(
                "Message from %s (ID: %s) in DMs : %s",
                event.message.author.username,
                event.message.author.id,
                event.message.content,
            )

    # Create a set of commands to define daily tasks
    async def load_reminders(self):
        """
        Load reminders from a JSON file and populate the reminders dictionary.
        """
        try:
            with open(
                f"{config['misc']['dataFolder']}/taskreminders.json",
                "r",
                encoding="utf-8",
            ) as file:
                reminders_data = json.load(file)
                for remind_time_str, user_reminders in reminders_data.items():
                    remind_time = datetime.strptime(
                        remind_time_str, "%Y-%m-%d %H:%M:%S"
                    )
                    reminders[remind_time] = user_reminders
            logger.debug(reminders)
        except FileNotFoundError:
            pass

    async def save_reminders(self):
        reminders_data = {}
        for remind_time, user_reminders in reminders.items():
            reminders_data[remind_time.strftime("%Y-%m-%d %H:%M:%S")] = user_reminders
        with open(
            f"{config['misc']['dataFolder']}/taskreminders.json", "w", encoding="utf-8"
        ) as file:
            json.dump(reminders_data, file, indent=4)

    # Set reminder
    @slash_command(
        name="reminder",
        sub_cmd_name="set",
        description="Gère les rappels pour voter",
        sub_cmd_description="Ajoute un rappel",
        scopes=enabled_servers,
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
        frequency: str = None,
        date=None,
    ):
        # Create the reminder time from the provided date, hour, and minute
        current_time = datetime.now()
        remind_time = datetime.strptime(f"{hour}:{minute}", "%H:%M")
        # Set the remind_time for today with the provided hour and minute
        remind_time = current_time.replace(
            hour=remind_time.hour,
            minute=remind_time.minute,
            second=0,
            microsecond=0,
        )
        if date:
            try:
                parseddate = datetime.strptime(date, "%d/%m/%Y")
            except ValueError:
                await ctx.send(
                    "Format de date invalide. Utilisez JJ/MM/AAAA", ephemeral=True
                )
                return
            remind_time = remind_time.replace(
                year=parseddate.year, month=parseddate.month, day=parseddate.day
            )

        # If the remind_time is in the past, set it for the next day
        while remind_time <= current_time:
            if frequency is None:
                remind_time += timedelta(days=1)
                if remind_time <= current_time:
                    await ctx.send(f"Le rappel ne peut pas être dans le passé")
                break
            elif frequency == "daily":
                remind_time += timedelta(days=1)
            elif frequency == "weekly":
                remind_time += timedelta(weeks=1)
            elif frequency == "monthly":
                remind_time += relativedelta(months=1)
            elif frequency == "yearly":
                remind_time += relativedelta(years=1)

        # Check if there are reminders for this remind_time, if not, initialize it
        if remind_time not in reminders:
            reminders[remind_time] = {}

        # Get the user's ID
        user_id = str(ctx.author.id)

        # Check if there are reminders for this user at this remind_time, if not, initialize it
        if user_id not in reminders[remind_time]:
            reminders[remind_time][user_id] = []

        # Append the new reminder for this user at this remind_time
        reminders[remind_time][user_id].append(
            {"message": task, "frequency": frequency}
        )

        # Save the reminders
        await self.save_reminders()

        # Send confirmation message
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
        buttons = []
        uuid_reminder_map = {}
        # Iterate through reminders to find user's reminders
        for remind_time, user_reminders in reminders.items():
            if user_id in user_reminders:
                for reminder in user_reminders[user_id]:
                    reminder_id = str(uuid.uuid4())
                    logger.debug(reminder)
                    if reminder["frequency"] == "daily":
                        label = f"{reminder['message']:40} (Tous les jours à {remind_time.strftime('%H:%M')})"
                    elif reminder["frequency"] == "weekly":
                        label = f"{reminder['message']:40} (Tous les {remind_time.strftime('%A')} à {remind_time.strftime('%H:%M')})"
                    elif reminder["frequency"] == "monthly":
                        label = f"{reminder['message']:40} (Tous les {remind_time.strftime('%d')} à {remind_time.strftime('%H:%M')})"
                    elif reminder["frequency"] == "yearly":
                        label = f"{reminder['message']:40} (Tous les {remind_time.strftime('%d/%m')} à {remind_time.strftime('%H:%M')})"
                    else:
                        label = f"{reminder['message']:40} ({remind_time.strftime('%H:%M')})"
                    buttons.append(
                        Button(
                            label=label,
                            style=ButtonStyle.SECONDARY,
                            custom_id=reminder_id,
                        )
                    )
                    # Map UUID to reminder
                    uuid_reminder_map[reminder_id] = (remind_time, reminder)

        if not buttons:
            await ctx.send("Tu n'as aucun rappel", ephemeral=True)
            return

        # Send message with reminder buttons
        message = await ctx.send(f"Quel rappel veux-tu supprimer (annulation {timestamp_converter(datetime.now()+timedelta(seconds=60)).format(TimestampStyles.RelativeTime)})",
            components=[ActionRow(*buttons)],
            ephemeral=True,
        )

        try:
            # Wait for user to click a button
            button_ctx = await self.bot.wait_for_component(
                components=[button.custom_id for button in buttons], timeout=60
            )
            ctx = button_ctx.ctx
            # Find selected reminder using UUID
            selected_reminder_data = uuid_reminder_map.get(ctx.custom_id)

            if selected_reminder_data:
                remind_time, selected_reminder = selected_reminder_data
                # Remove selected reminder
                reminders[remind_time][user_id].remove(selected_reminder)
                if not reminders[remind_time][user_id]:
                    del reminders[remind_time][user_id]
                    if not reminders[remind_time]:
                        del reminders[remind_time]
                await self.save_reminders()
                await ctx.edit_origin(
                    content=f"Rappel à {remind_time.strftime('%H:%M')} supprimé.",
                    components=[],
                )
                logger.info(
                    "Reminder at %s deleted for %s",
                    remind_time.strftime("%H:%M"),
                    ctx.author.display_name,
                )
            else:
                await ctx.send("Rappel introuvable", ephemeral=True)
        except asyncio.TimeoutError:
            await ctx.edit(message=message,
                content="Annulé, aucun rappel sélectionné", components=[]
            )

    @Task.create(IntervalTrigger(minutes=1))
    async def check_reminders(self):
        current_time = datetime.now()
        reminders_to_remove = set()
        reminders_to_add = {}

        for remind_time, user_reminders in reminders.copy().items():
            if remind_time <= current_time:
                for user_id, reminder_list in user_reminders.items():
                    user = await self.bot.fetch_user(user_id)
                    for reminder in reminder_list:
                        await user.send(reminder["message"])
                        logger.info(
                            f"Reminder sent to {user.global_name}: {reminder['message']}"
                        )
                reminders_to_remove.add(remind_time)
                frequency = reminder.get("frequency")
                if frequency == "daily":
                    reminders_to_add[remind_time + timedelta(days=1)] = user_reminders
                elif frequency == "weekly":
                    reminders_to_add[remind_time + timedelta(weeks=1)] = user_reminders
                elif frequency == "monthly":
                    reminders_to_add[remind_time + relativedelta(months=1)] = (
                        user_reminders
                    )
                elif frequency == "yearly":
                    reminders_to_add[remind_time + relativedelta(years=1)] = (
                        user_reminders
                    )

        if reminders_to_remove:
            for remind_time in reminders_to_remove:
                del reminders[remind_time]
        if reminders_to_add:
            reminders.update(reminders_to_add)
        if reminders_to_remove or reminders_to_add:
            await self.save_reminders()
