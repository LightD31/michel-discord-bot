import os
import random
from datetime import datetime, timedelta
from typing import Optional

import pymongo
import pytz
from babel.dates import format_date, get_date_format
from interactions import (
    AutocompleteContext,
    Client,
    ComponentContext,
    Embed,
    Extension,
    Message,
    OptionType,
    OrTrigger,
    SlashContext,
    Task,
    TimeTrigger,
    Member,
    User,
    listen,
    slash_command,
    slash_option,
)
from interactions.ext import paginators

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleBirthday")


class BirthdayClass(Extension):
    def __init__(self, bot):
        self.bot: Client = bot
        # Database connection
        client = pymongo.MongoClient(config["mongodb"]["url"])
        db = client["Playlist"]
        self.collection = db["birthday"]

    @listen()
    async def on_startup(self):
        self.anniversaire_check.start()

    @slash_command(
        name="anniversaire",
        description="Anniversaire",
        scopes=enabled_servers,
        sub_cmd_name="ajouter",
        sub_cmd_description="Ajoute ou modifie ton anniversaire",
    )
    @slash_option(
        name="date",
        description="Date de l'anniversaire (format: JJ/MM/AAAA)",
        opt_type=OptionType.STRING,
        required=True,
        min_length=10,
        max_length=10,
    )
    @slash_option(
        name="timezone",
        description="Fuseau horaire ex: Europe/Paris",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="hideyear",
        description="Masquer l'année",
        opt_type=OptionType.BOOLEAN,
        required=False,
    )
    async def anniversaire(
        self,
        ctx: SlashContext,
        date: str,
        timezone: str,
        hideyear: Optional[bool] = False,
    ):
        # Try to parse the date
        try:
            date = datetime.strptime(date, "%d/%m/%Y")
        except ValueError:
            await ctx.send("Date invalide", ephemeral=True)
            return

        if timezone not in pytz.all_timezones:
            await ctx.send("Fuseau horaire invalide", ephemeral=True)
            return
        timezone = pytz.timezone(timezone)
        # Check if already in database
        if self.collection.find_one({"user": ctx.author.id, "server": ctx.guild.id}):
            self.collection.update_one(
                {"user": ctx.author.id, "server": ctx.guild.id},
                {
                    "$set": {
                        "date": date,
                        "timezone": timezone.zone,
                        "hideyear": hideyear,
                    }
                },
            )
            await ctx.send("Anniversaire mis à jour", ephemeral=True)
            logger.info(
                "Anniversaire de %s mis à jour sur le serveur %s (%s)",
                ctx.author.display_name,
                ctx.guild.name,
                date.strftime("%d/%m/%Y"),
            )
            return
        # Add to database
        self.collection.insert_one(
            {
                "user": ctx.author.id,
                "server": ctx.guild.id,
                "date": date,
                "timezone": timezone.zone,
                "hideyear": hideyear,
                "isBirthday": False,
            }
        )
        logger.info(
            "Anniversaire de %s ajouté sur le serveur %s (%s)",
            ctx.author.display_name,
            ctx.guild.name,
            date.strftime("%d/%m/%Y"),
        )
        await ctx.send("Anniversaire ajouté", ephemeral=True)

    @anniversaire.autocomplete("timezone")
    async def anniversaire_timezone(self, ctx: AutocompleteContext):
        timezone_imput = ctx.input_text.lower()
        timezones = pytz.all_timezones
        if timezone_imput:
            timezones = [
                timezone for timezone in timezones if timezone_imput in timezone.lower()
            ]
            # Limit the number of choices to 25
            timezones = timezones[:25]
        else:
            timezones = random.sample(timezones, 25)
        await ctx.send(
            choices=[
                {
                    "name": timezone,
                    "value": timezone,
                }
                for timezone in timezones
            ]
        )

    @anniversaire.subcommand(
        sub_cmd_name="supprimer",
        sub_cmd_description="Supprime ton anniversaire sur ce serveur",
    )
    async def anniversaire_supprimer(self, ctx: SlashContext):
        # Remove from database
        self.collection.delete_one({"user": ctx.author.id, "server": ctx.guild.id})
        await ctx.send("Anniversaire supprimé", ephemeral=True)

    @anniversaire.subcommand(
        sub_cmd_name="purge",
        sub_cmd_description="Supprime ton anniversaire sur tous les serveurs",
    )
    async def anniversaire_purge(self, ctx: SlashContext):
        # Remove from database
        self.collection.delete_many({"user": ctx.author.id})
        await ctx.send("Anniversaire supprimé sur tous les serveurs", ephemeral=True)

    @anniversaire.subcommand(
        sub_cmd_name="liste",
        sub_cmd_description="Liste des anniversaires",
    )
    async def anniversaire_liste(self, ctx: SlashContext):
        # Get all birthdays
        birthdays = self.collection.find({"server": ctx.guild.id})
        # Get locale
        locale = module_config[str(ctx.guild.id)].get("birthdayGuildLocale", "en_US")
        date_format = str(get_date_format("long", locale=locale))
        # remove the year from the date format
        date_format = date_format.replace("y", "").strip()

        # Create embed
        i = 0
        embeds = []
        embed = Embed(
            title="Anniversaires",
            description="Liste des anniversaires",
            color=0x00FF00,
        )
        birthday_list = ""
        # Sort by date without taking the year into account
        birthdays = sorted(
            birthdays,
            key=lambda x: x["date"].replace(year=2000),
        )
        for birthday in birthdays:
            date: datetime = birthday["date"]
            user: User = await self.bot.fetch_user(birthday["user"])
            hideyear: bool = birthday.get("hideyear", False)
            if hideyear:
                birthday_list += f"**{user.mention}** : {format_date(date,date_format, locale=locale)}\n"
            else:
                birthday_list += f"**{user.mention}** : {format_date(date,date_format, locale=locale)} ({datetime.now().year - date.year} ans)\n"
            if i % 25 == 0 and i != 0:
                embed.description = birthday_list
                embeds.append(embed)
                embed = Embed(
                    title="Anniversaires",
                    description="",
                    color=0x00FF00,
                )
                birthday_list = ""
            i += 1
        embed.description = birthday_list
        embeds.append(embed)
        paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
        await paginator.send(ctx)

    @Task.create(OrTrigger(*[TimeTrigger(i, j) for i in range(24) for j in [0, 30]]))
    # @Task.create(TimeTrigger(0, 14, 10, utc=False))
    async def anniversaire_check(self):
        # Get today's date
        today = datetime.now(pytz.UTC).replace(second=0, microsecond=0)
        # Get all birthdays
        birthdays = self.collection.find()
        for birthday in birthdays:
            date: datetime = birthday["date"]
            timezone = pytz.timezone(birthday["timezone"])
            date = timezone.localize(date)
            logger.debug(
                "debug\nnow as birthday tz: %s\ndate as birthday tz: %s",
                today.astimezone(timezone),
                date.replace(year=today.year),
            )
            if (
                today.astimezone(timezone).date()
                == date.replace(year=today.year).date()
            ):
                # Check if birthday is already marked as birthday
                logger.debug("It's %s's birthday", birthday["user"])
                if birthday["isBirthday"]:
                    logger.debug(
                        "Birthday already marked as birthday for %s", birthday["user"]
                    )
                    continue
                # Mark as birthday
                self.collection.update_one(
                    {"user": birthday["user"], "server": birthday["server"]},
                    {"$set": {"isBirthday": True}},
                )
                # Get server
                server = await self.bot.fetch_guild(birthday["server"])
                # Get member
                member = await server.fetch_member(birthday["user"])
                # Get channel
                channel = module_config[str(birthday["server"])].get(
                    "birthdayChannelId", None
                )
                if channel:
                    channel = await server.fetch_channel(channel)
                else:
                    channel = server.system_channel
                # Get personnalised message
                messages = module_config[str(birthday["server"])].get(
                    "birthdayMessageList", ["Joyeux anniversaire {mention} ! 🎉"]
                )
                weights = module_config[str(birthday["server"])].get(
                    "birthdayMessageWeights", len(messages) * [1]
                )
                message = (
                    random.choices(messages, weights)[0]
                )
                # Send message
                message = message.format(mention=member.mention, age=today.year - date.year)
                logger.info(
                    "C'est l'anniversaire de %s sur le serveur %s (%s ans)",
                    member.display_name,
                    server.name,
                    today.year - date.year,
                )
                await channel.send(message)
                # Give role if defined
                role = module_config[str(birthday["server"])].get(
                    "birthdayRoleId", None
                )
                if role:
                    role = await server.fetch_role(role)
                    await member.add_role(role)
                    logger.info(
                        "Rôle %s donné à %s sur le serveur %s",
                        role.name,
                        member.display_name,
                        server.name,
                    )
            else:
                # Check if birthday is already marked as not birthday
                if not birthday.get("isBirthday", True):
                    continue
                # Mark as not birthday
                self.collection.update_one(
                    {"user": birthday["user"], "server": birthday["server"]},
                    {"$set": {"isBirthday": False}},
                )

                # Get server
                server = await self.bot.fetch_guild(birthday["server"])
                # Get member
                member = await server.fetch_member(birthday["user"])
                logger.info(
                    "Ce n'est plus l'anniversaire de %s sur le serveur %s",
                    member.display_name,
                    server.name,
                )
                # Get role
                role = module_config[str(birthday["server"])].get(
                    "birthdayRoleId", None
                )
                if role:
                    role = await server.fetch_role(role)
                    if role in member.roles:
                        await member.remove_role(role)
                        logger.info(
                            "Rôle %s retiré à %s sur le serveur %s",
                            role.name,
                            member.display_name,
                            server.name,
                        )


class CustomPaginator(paginators.Paginator):
    # Override the functions here
    async def _on_button(
        self, ctx: ComponentContext, *args, **kwargs
    ) -> Optional[Message]:
        if self._timeout_task:
            self._timeout_task.ping.set()
        match ctx.custom_id.split("|")[1]:
            case "first":
                self.page_index = 0
            case "last":
                self.page_index = len(self.pages) - 1
            case "next":
                if (self.page_index + 1) < len(self.pages):
                    self.page_index += 1
            case "back":
                if self.page_index >= 1:
                    self.page_index -= 1
            case "select":
                self.page_index = int(ctx.values[0])
            case "callback":
                if self.callback:
                    return await self.callback(ctx)

        await ctx.edit_origin(**self.to_dict())
        return None
