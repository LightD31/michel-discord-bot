import os
import random
from datetime import datetime
from typing import Optional

import pymongo
import pytz
from interactions import (
    BaseChannel,
    Client,
    ComponentContext,
    Embed,
    Extension,
    Guild,
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
from interactions.api.events import MessageCreate
from interactions.ext import paginators

from src import logutil
from src.utils import format_number, load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleXp")


class XP(Extension):
    def __init__(self, bot: client):
        self.bot: Client = bot
        # Connect to MongoDB
        mongo_client = pymongo.MongoClient(config.get("mongodb", "").get("url", ""))
        self.db = mongo_client["Playlist"]
        logger.debug("enabled_servers for XP module: %s", enabled_servers)

    @listen()
    async def on_startup(self):
        self.leaderboardpermanent.start()
        await self.leaderboardpermanent()

    @listen()
    async def on_message(self, event: MessageCreate):
        """
        A listener that gives XP to a user when they send a message using MongoDB to store levels.

        Parameters:
        -----------
        ctx : interactions.MessageContext
            The context of the message.
        """

        logger.debug(
            "Author: %s, Guild: %s, Channel: %s, Message: %s, Bot: %s",
            event.message.author,
            event.message.guild,
            event.message.channel,
            event.message.content,
            event.message.author.bot,
        )

        if event.message.guild is None or event.message.guild is []:
            return
        user = str(event.message.author.id)
        guild = str(event.message.guild.id)
        if event.message.author.bot is True:
            logger.debug("Message was from a bot.")
            return
        if guild not in enabled_servers:
            logger.debug("Message was not from a guild with XP enabled.")
            return
        # Create a new entry for the guild if it doesn't exist.
        if guild not in self.db.list_collection_names():
            self.db.create_collection(guild)
            logger.debug("Created a new collection for %s.", event.message.guild.name)
        # Find the user in the database and create a new entry if they don't exist.
        stats = self.db[guild].find_one({"_id": user})
        if stats is None:
            newuser = {
                "_id": user,
                "xp": random.randint(15, 25),
                "time": event.message.created_at.timestamp(),
                "msg": 1,
                "lvl": 0,
            }
            self.db[guild].insert_one(newuser)
            logger.debug("Added %s to the database.", user)
        else:
            if event.message.created_at.timestamp() - stats["time"] < 60:
                logger.debug("No XP given to %s due to cooldown.", user)
                return
            # Add XP to the user and update the database.
            xp = stats["xp"] + random.randint(15, 25)
            msg = stats["msg"] + 1
            self.db[str(guild)].update_one(
                {"_id": user},
                {
                    "$set": {
                        "xp": xp,
                        "time": event.message.created_at.timestamp(),
                        "msg": msg,
                    }
                },
            )
            logger.debug("Gave %s XP.", user)
            calculate_level_result = await self.calculate_level(xp)
            lvl = calculate_level_result[0]
            oldlvl = stats["lvl"]
            if lvl > oldlvl:
                self.db[guild].update_one(
                    {"_id": user}, {"$set": {"lvl": lvl}}, upsert=True
                )
                logger.debug("%s is now level {lvl}.", user)
                # Send a message if the user levels up.
                lvlupmessages = module_config[guild].get(
                    "levelUpMessageList",
                    ["Bravo {mention}, tu as atteint le niveau {lvl} !"],
                )
                weights = module_config[guild].get("levelUpMessageWeights", len(lvlupmessages) * [1])
                message = random.choices(lvlupmessages, weights=weights)[0]
                logger.debug("Messages: %s\nWeights: %s", message, weights)
                filled_message = message.format(
                    mention=event.message.author.mention,
                    lvl=lvl,
                )
                await event.message.channel.send(filled_message)

    @slash_command(
        name="rank",
        description="Affiche le niveau et l'XP d'un utilisateur",
        scopes=enabled_servers,
    )
    @slash_option(
        "utilisateur",
        "Utilisateur dont afficher le niveau et l'XP",
        opt_type=OptionType.USER,
        required=False,
    )
    async def rank(
        self,
        ctx: SlashContext,
        utilisateur: User = None,
    ):
        """
        A slash command that displays the level and XP of a user.

        Parameters:
        -----------
        ctx : interactions.SlashContext
            The context of the slash command.
        utilisateur : interactions.User, optional
            The user to display the level and XP of.
        """
        if utilisateur is None:
            utilisateur = ctx.author
        stats = self.db[str(ctx.guild.id)].find_one({"_id": str(utilisateur.id)})
        if stats is None:
            await ctx.send(f"{utilisateur.mention} n'a pas encore de niveau.")
        else:
            xp = stats["xp"]
            lvl, xp_in_level, xp_max = await self.calculate_level(xp)
            boxes = int(round((xp_in_level / xp_max) * 10, 0))
            rankings = self.db[str(ctx.guild.id)].find().sort("xp", -1)
            rank = 0
            for x in rankings:
                rank += 1
                if str(utilisateur.id) == x["_id"]:
                    break
            embed = Embed(
                title=f"Statistiques de {utilisateur.username}",
                color=0x00FF00,
                timestamp=datetime.now(pytz.timezone("Europe/Paris")),
            )
            embed.add_field(name="Name", value=utilisateur.mention, inline=True)
            embed.add_field(name="Niveau", value=lvl, inline=True)
            embed.add_field(
                name="Rank", value=f"{rank}/{ctx.guild.member_count}", inline=True
            )
            embed.add_field(name="XP", value=f"{xp_in_level}/{xp_max}", inline=True)

            embed.add_field(name="Messages", value=stats["msg"], inline=True)

            embed.add_field(
                name="Progression",
                value=boxes * ":blue_square:" + (10 - boxes) * ":white_large_square:",
                inline=False,
            )
            embed.set_thumbnail(url=utilisateur.avatar_url)
            await ctx.send(embed=embed)

    @slash_command(
        name="leaderboard",
        description="Affiche le classement des utilisateurs",
        scopes=enabled_servers,
    )
    async def leaderboard(self, ctx: SlashContext):
        """
        A slash command that displays the leaderboard of users.

        Parameters:
        -----------
        ctx : interactions.SlashContext
            The context of the slash command.
        """
        rankings = self.db[str(ctx.guild.id)].find().sort("xp", -1)
        i = 0
        embeds = []
        embed = Embed(
            title=f"Classement de {ctx.guild.name}",
            color=0x00FF00,
            timestamp=datetime.now(pytz.timezone("Europe/Paris")),
        )
        for x in rankings:
            try:
                temp = ctx.guild.get_member(x["_id"])
                if temp is None:
                    temp = await ctx.bot.fetch_user(x["_id"])
                    if temp is None:
                        temp["username"] = "Utilisateur inconnu"
                        temp["display_name"] = "Utilisateur inconnu"
                tempxp = x["xp"]
                lvl, xp_in_level, xp_max = await self.calculate_level(tempxp)
                if i == 0 or i == 1 or i == 2:
                    text = ["🥇", "🥈", "🥉"][i]
                else:
                    text = f"{i+1} -"

                temppercentage = f"({(xp_in_level/xp_max*100):.0f}"
                embed.add_field(
                    name=f"{text} {temp.display_name} ({temp.username})",
                    value=f"`Niveau {str(lvl).rjust(2)} {temppercentage.rjust(3)} %) | XP : {str(format_number(tempxp).rjust(7))} | {str(format_number(x['msg'])).rjust(5)} messages`",
                    inline=False,
                )
                if (
                    i % 10 == 0 and i != 0
                ):  # If we've added 10 fields, create a new embed
                    embeds.append(embed)
                    embed = Embed(
                        title=f"Classement de {ctx.guild.name} (cont.)",
                        color=0x00FF00,
                        timestamp=datetime.now(pytz.timezone("Europe/Paris")),
                    )
                i += 1
            except KeyError:
                pass
        embeds.append(embed)  # Add the last embed

        paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
        # add the callback button and function to update the leaderboard
        await paginator.send(ctx)

    @Task.create(TimeTrigger(utc=False))
    async def leaderboardpermanent(self):
        """
        A function that update the leaderboard of users.
        """
        for guild in enabled_servers:
            if (
                module_config[guild].get("xpChannelId") is None
                or module_config[guild].get("xpMessageId") is None
            ):
                logger.debug("No leaderboard message found for %s", guild)
                continue
            guild: Guild = await self.bot.fetch_guild(guild)
            channel: BaseChannel = await guild.fetch_channel(
                module_config[str(guild.id)]["xpChannelId"]
            )
            message: Message = await channel.fetch_message(
                module_config[str(guild.id)]["xpMessageId"]
            )
            rankings = self.db[str(guild.id)].find().sort("xp", -1)
            i = 0
            embeds = []
            embed = Embed(
                title=f"Classement de {guild.name}",
                color=0x00FF00,
                timestamp=datetime.now(pytz.timezone("Europe/Paris")),
            )
            for x in rankings:
                try:
                    temp = guild.get_member(x["_id"])
                    if temp is None:
                        temp = await self.bot.fetch_user(x["_id"])
                        if temp is None:
                            temp["username"] = "Utilisateur inconnu"
                            temp["display_name"] = "Utilisateur inconnu"
                    tempxp = x["xp"]
                    lvl, xp_in_level, xp_max = await self.calculate_level(tempxp)
                    if i == 0 or i == 1 or i == 2:
                        text = ["🥇", "🥈", "🥉"][i]
                    else:
                        text = f"{i+1} -"

                    temppercentage = f"({(xp_in_level/xp_max*100):.0f}"
                    embed.add_field(
                        name=f"{text} {temp.display_name} ({temp.username})",
                        value=f"`Niveau {str(lvl).rjust(2)} {temppercentage.rjust(3)} %) | XP : {str(format_number(tempxp).rjust(7))} | {str(format_number(x['msg'])).rjust(5)} messages`",
                        inline=False,
                    )
                    if (
                        i % 10 == 0 and i != 0
                    ):  # If we've added 10 fields, create a new embed
                        embeds.append(embed)
                        embed = Embed(
                            title=f"Classement de {guild.name} (cont.)",
                            color=0x00FF00,
                            timestamp=datetime.now(pytz.timezone("Europe/Paris")),
                        )
                    i += 1
                except KeyError:
                    pass
            embeds.append(embed)  # Add the last embed

            paginator = CustomPaginator.create_from_embeds(self.bot, *embeds)
            # add the callback button and function to update the leaderboard
            logger.debug("Updating leaderboard for %s", guild.name)
            await message.edit(
                content="",
                embeds=paginator.to_dict()["embeds"],
                components=paginator.to_dict()["components"],
            )

    async def calculate_level(self, xp):
        """
        Calculates the level, XP within the level, and maximum XP for the given XP value.
        XP required to level up is 5x^2 + 50x  + 100 where x is the level.

        Args:
            xp (int): The XP value to calculate the level for.

        Returns:
            tuple: A tuple containing the level, XP within the level, and maximum XP for the level.
        """
        level = 0
        while True:
            if xp < ((5 * (level**2)) + (50 * level) + 100):
                break
            else:
                xp -= (5 * ((level) ** 2)) + (50 * (level) + 100)
            level += 1
        xp_max = (5 * (level**2)) + (50 * level) + 100
        xp_in_level = xp
        return level, xp_in_level, xp_max


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
