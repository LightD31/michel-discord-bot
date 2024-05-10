import json
import os
from datetime import datetime, timedelta

import pytz
import requests
from dotenv import load_dotenv
from interactions import (
    ActionRow,
    BaseChannel,
    Button,
    ButtonStyle,
    Extension,
    IntervalTrigger,
    Message,
    OptionType,
    SlashContext,
    Task,
    TimeTrigger,
    User,
    client,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import Component

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))

load_dotenv()

config, module_config, enabled_servers = load_config("moduleColoc")

# Server specific module
module_config = module_config[enabled_servers[0]]

# Keep track of reminders
reminders = {}


class ColocClass(Extension):
    def __init__(self, bot: client):
        self.bot: client = bot

    @listen()
    async def on_startup(self):
        self.journa.start()
        await self.load_reminders()
        self.check_reminders.start()

    @slash_command(name="fesse", description="Fesses", scopes=enabled_servers)
    async def fesse(self, ctx: SlashContext):
        await ctx.send(
            "https://media1.tenor.com/m/YIUbUoKi8ZcAAAAC/sesame-street-kermit-the-frog.gif"
        )

    @slash_command(
        name="massageducul",
        description="Massage du cul",
        scopes=enabled_servers,
    )
    async def massageducul(self, ctx: SlashContext):
        await ctx.send("https://media1.tenor.com/m/h6OvENNtJh0AAAAC/bebou.gif")

    @Task.create(TimeTrigger(22, utc=False))
    async def journa(self):
        channel: BaseChannel = await self.bot.fetch_channel(
            module_config["colocZuniversChannelId"]
        )
        paris_tz = pytz.timezone("Europe/Paris")
        message: Message = (await channel.history(limit=1).flatten())[0]
        logger.debug(
            "Checking if message %s was posted today (message timestamp: %s today: %s",
            message.id,
            message.created_at.astimezone(paris_tz).strftime("%Y-%m-%d %H:%M:%S %Z"),
            datetime.now(pytz.UTC)
            .astimezone(paris_tz)
            .strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
        if (
            message.created_at.astimezone(paris_tz).date()
            == datetime.now(paris_tz).date()
        ):
            logger.info(
                "Channel already posted today, skipping (message date: %s today: %s)",
                message.created_at.astimezone(paris_tz).date(),
                datetime.now(paris_tz).date(),
            )
            return
        await channel.send(
            ":robot: <@&934560421912911882>, heureusement que les robots n'oublient pas ! :robot:"
        )

    # Zunivers API
    async def load_reminders(self):
        """
        Load reminders from a JSON file and populate the reminders dictionary.
        """
        try:
            with open(
                f"{config['misc']['dataFolder']}/journa.json", "r", encoding="utf-8"
            ) as file:
                reminders_data = json.load(file)
                for remind_time_str, user_ids in reminders_data.items():
                    remind_time = datetime.strptime(
                        remind_time_str, "%Y-%m-%d %H:%M:%S"
                    )
                    reminders[remind_time] = set(user_ids)
        except FileNotFoundError:
            pass

    async def save_reminders(self):
        reminders_data = {
            remind_time.strftime("%Y-%m-%d %H:%M:%S"): list(user_ids)
            for remind_time, user_ids in reminders.items()
        }
        with open(
            f"{config['misc']['dataFolder']}/journa.json", "w", encoding="utf-8"
        ) as file:
            json.dump(reminders_data, file, indent=4)

    # Set reminder to /journa
    @slash_command(
        name="journa",
        sub_cmd_name="set",
        description="Gère les rappels pour voter",
        sub_cmd_description="Ajoute un rappel pour /journa",
        scopes=enabled_servers,
    )
    @slash_option(
        name="heure",
        description="Heure du rappel",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=23,
    )
    @slash_option(
        "minute",
        "Minute du rappel",
        OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=59,
    )
    async def rappelvote_set(self, ctx: SlashContext, heure: int, minute: int):
        remind_time = datetime.strptime(f"{heure}:{minute}", "%H:%M")
        current_time = datetime.now()
        remind_time = current_time.replace(
            hour=remind_time.hour,
            minute=remind_time.minute,
            second=0,
            microsecond=0,
        )
        if remind_time <= current_time:
            remind_time += timedelta(days=1)
        if remind_time not in reminders:
            reminders[remind_time] = set()
        reminders[remind_time].add(ctx.user.id)
        await self.save_reminders()
        await ctx.send(
            f"Rappel défini à {remind_time.strftime('%H:%M')}.", ephemeral=True
        )
        logger.info("%s a a jouté un rappel à %s", ctx.user.username, remind_time)

    @rappelvote_set.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Supprime un rappel pour /journa",
    )
    async def deletereminder(self, ctx: SlashContext):
        user_id = ctx.user.id
        # create the list of reminders for the user
        reminders_list = []
        for remind_time, user_ids in reminders.copy().items():
            if user_id in user_ids:
                reminders_list.append(remind_time)
        # Create a button for each reminder
        buttons = [
            Button(
                label=remind_time.strftime("%H:%M"),
                style=ButtonStyle.SECONDARY,
                custom_id=str(remind_time.timestamp()),
            )
            for remind_time in reminders_list
        ]
        # Send a message with the buttons
        message = await ctx.send(
            "Quel rappel veux-tu supprimer ?",
            components=[ActionRow(*buttons)],
            ephemeral=True,
        )
        try:
            # Wait for the user to click a button
            button_ctx: Component = await self.bot.wait_for_component(
                components=[
                    str(remind_time.timestamp()) for remind_time in reminders_list
                ],
                timeout=60,
            )
            # Remove the reminder from the reminders dictionary
            remind_time = datetime.fromtimestamp(float(button_ctx.ctx.custom_id))
            reminders[remind_time].remove(user_id)
            if not reminders[remind_time]:
                del reminders[remind_time]
            # Save the reminders to a JSON file
            await self.save_reminders()
            # Send a message to the user indicating that the reminder has been removed
            await button_ctx.ctx.edit_origin(
                content=f"Rappel à {remind_time.strftime('%H:%M')} supprimé.",
                components=[],
            )
            logger.info(
                "Rappel à %s supprimé pour %s",
                remind_time.strftime("%H:%M"),
                ctx.user.display_name,
            )
        except TimeoutError:
            await ctx.send(
                "Tu n'as pas sélectionné de rappel à supprimer.", ephemeral=True
            )
            await message.edit(content="Aucun rappel sélectionné.", components=[])

    @Task.create(IntervalTrigger(minutes=1))
    async def check_reminders(self):
        current_time = datetime.now()
        reminders_to_remove = []
        for remind_time, user_ids in reminders.copy().items():
            if remind_time <= current_time:
                for user_id in user_ids.copy():
                    user: User = await self.bot.fetch_user(user_id)
                    # Check if the user did /journa today
                    response = requests.get(
                        f"https://zunivers-api.zerator.com/public/loot/{user.username}",
                        timeout=5,
                    )
                    for day in response.json()["lootInfos"]:
                        if day["date"] == current_time.strftime("%Y-%m-%d"):
                            if day["count"] == 0:
                                await user.send(
                                    "Tu n'as pas encore /journa aujourd'hui, n'oublie pas !\nhttps://discord.com/channels/138283154589876224/808432657838768168"
                                )
                                logger.info("Rappel envoyé à %s", user.display_name)
                            else:
                                logger.info(
                                    "Pas de rappel pour %s, /journa déjà fait aujourd'hui.",
                                    user.display_name,
                                )
                    next_remind_time = remind_time + timedelta(days=1)
                    if next_remind_time not in reminders:
                        reminders[next_remind_time] = set()
                    reminders[next_remind_time].add(user_id)
                    user_ids.remove(user_id)
                if not user_ids:
                    reminders_to_remove.append(remind_time)
        for remind_time in reminders_to_remove:
            del reminders[remind_time]
        await self.save_reminders()
