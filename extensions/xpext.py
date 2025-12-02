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

# Constants
XP_COOLDOWN_SECONDS = 60
XP_MIN = 15
XP_MAX = 25
EMBED_COLOR = 0x00FF00
TIMEZONE = pytz.timezone("Europe/Paris")
LEADERBOARD_PAGE_SIZE = 10
RANK_MEDALS = ["🥇", "🥈", "🥉"]
DEFAULT_LEVEL_UP_MESSAGE = "Bravo {mention}, tu as atteint le niveau {lvl} !"


def calculate_level(xp: int) -> tuple[int, int, int]:
    """
    Calculate level, XP within the level, and maximum XP for the given XP value.
    XP required to level up is 5x^2 + 50x + 100 where x is the level.

    Args:
        xp: The total XP value.

    Returns:
        A tuple containing (level, xp_in_level, xp_max).
    """
    level = 0
    remaining_xp = xp
    while True:
        xp_for_level = (5 * (level ** 2)) + (50 * level) + 100
        if remaining_xp < xp_for_level:
            break
        remaining_xp -= xp_for_level
        level += 1
    xp_max = (5 * (level ** 2)) + (50 * level) + 100
    return level, remaining_xp, xp_max


def get_rank_display(rank: int) -> str:
    """Get the display string for a rank (medal or number)."""
    if rank < len(RANK_MEDALS):
        return RANK_MEDALS[rank]
    return f"{rank + 1} -"


def create_leaderboard_embed(guild_name: str, is_continuation: bool = False) -> Embed:
    """Create a new leaderboard embed."""
    title = f"Classement de {guild_name}"
    if is_continuation:
        title += " (cont.)"
    return Embed(
        title=title,
        color=EMBED_COLOR,
        timestamp=datetime.now(TIMEZONE),
    )


class XP(Extension):
    """XP and leveling system extension for Discord."""

    def __init__(self, bot: client):
        self.bot: Client = bot
        mongo_client = pymongo.MongoClient(config.get("mongodb", {}).get("url", ""))
        self.db = mongo_client["Playlist"]
        logger.debug("enabled_servers for XP module: %s", enabled_servers)

    @listen()
    async def on_startup(self):
        self.leaderboardpermanent.start()
        await self.leaderboardpermanent()

    @listen()
    async def on_message(self, event: MessageCreate):
        """Give XP to a user when they send a message."""
        message = event.message
        
        if not self._is_valid_xp_message(message):
            return

        user_id = str(message.author.id)
        guild_id = str(message.guild.id)
        
        self._ensure_guild_collection(guild_id, message.guild.name)
        
        stats = self.db[guild_id].find_one({"_id": user_id})
        
        if stats is None:
            self._create_new_user(guild_id, user_id, message.created_at.timestamp())
            return
        
        if self._is_on_cooldown(message.created_at.timestamp(), stats["time"]):
            logger.debug("No XP given to %s due to cooldown.", user_id)
            return
        
        await self._update_user_xp(guild_id, user_id, stats, message)

    def _is_valid_xp_message(self, message: Message) -> bool:
        """Check if the message is valid for XP gain."""
        if message.guild is None:
            return False
        if message.author.bot:
            logger.debug("Message was from a bot.")
            return False
        if str(message.guild.id) not in enabled_servers:
            logger.debug("Message was not from a guild with XP enabled.")
            return False
        return True

    def _ensure_guild_collection(self, guild_id: str, guild_name: str) -> None:
        """Create a new collection for the guild if it doesn't exist."""
        if guild_id not in self.db.list_collection_names():
            self.db.create_collection(guild_id)
            logger.debug("Created a new collection for %s.", guild_name)

    def _create_new_user(self, guild_id: str, user_id: str, timestamp: float) -> None:
        """Create a new user entry in the database."""
        new_user = {
            "_id": user_id,
            "xp": random.randint(XP_MIN, XP_MAX),
            "time": timestamp,
            "msg": 1,
            "lvl": 0,
        }
        self.db[guild_id].insert_one(new_user)
        logger.debug("Added %s to the database.", user_id)

    def _is_on_cooldown(self, current_time: float, last_time: float) -> bool:
        """Check if the user is on XP cooldown."""
        return current_time - last_time < XP_COOLDOWN_SECONDS

    async def _update_user_xp(self, guild_id: str, user_id: str, stats: dict, message: Message) -> None:
        """Update user XP and handle level ups."""
        xp_gained = random.randint(XP_MIN, XP_MAX)
        new_xp = stats["xp"] + xp_gained
        new_msg_count = stats["msg"] + 1
        
        self.db[guild_id].update_one(
            {"_id": user_id},
            {"$set": {
                "xp": new_xp,
                "time": message.created_at.timestamp(),
                "msg": new_msg_count,
            }}
        )
        logger.debug("Gave %s XP.", user_id)
        
        new_level, _, _ = calculate_level(new_xp)
        old_level = stats["lvl"]
        
        if new_level > old_level:
            await self._handle_level_up(guild_id, user_id, new_level, message)

    async def _handle_level_up(self, guild_id: str, user_id: str, new_level: int, message: Message) -> None:
        """Handle user level up notification."""
        self.db[guild_id].update_one(
            {"_id": user_id}, 
            {"$set": {"lvl": new_level}}, 
            upsert=True
        )
        logger.debug("%s is now level %d.", user_id, new_level)
        
        guild_config = module_config.get(guild_id, {})
        level_up_messages = guild_config.get("levelUpMessageList", [DEFAULT_LEVEL_UP_MESSAGE])
        weights = guild_config.get("levelUpMessageWeights", [1] * len(level_up_messages))
        
        chosen_message = random.choices(level_up_messages, weights=weights)[0]
        formatted_message = chosen_message.format(
            mention=message.author.mention,
            lvl=new_level,
        )
        await message.channel.send(formatted_message)

    def _get_user_rank(self, guild_id: str, user_id: str) -> int:
        """Get the rank of a user in the guild."""
        rankings = self.db[guild_id].find().sort("xp", -1)
        for rank, entry in enumerate(rankings, start=1):
            if entry["_id"] == user_id:
                return rank
        return 0

    def _create_progress_bar(self, xp_in_level: int, xp_max: int, length: int = 10) -> str:
        """Create a visual progress bar."""
        filled = int(round((xp_in_level / xp_max) * length))
        return ":blue_square:" * filled + ":white_large_square:" * (length - filled)

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
    async def rank(self, ctx: SlashContext, utilisateur: User = None):
        """Display the level and XP of a user."""
        target_user = utilisateur or ctx.author
        guild_id = str(ctx.guild.id)
        
        stats = self.db[guild_id].find_one({"_id": str(target_user.id)})
        
        if stats is None:
            await ctx.send(f"{target_user.mention} n'a pas encore de niveau.")
            return
        
        xp = stats["xp"]
        lvl, xp_in_level, xp_max = calculate_level(xp)
        rank = self._get_user_rank(guild_id, str(target_user.id))
        
        embed = Embed(
            title=f"Statistiques de {target_user.username}",
            color=EMBED_COLOR,
            timestamp=datetime.now(TIMEZONE),
        )
        embed.add_field(name="Name", value=target_user.mention, inline=True)
        embed.add_field(name="Niveau", value=lvl, inline=True)
        embed.add_field(name="Rank", value=f"{rank}/{ctx.guild.member_count}", inline=True)
        embed.add_field(name="XP", value=f"{xp_in_level}/{xp_max}", inline=True)
        embed.add_field(name="Messages", value=stats["msg"], inline=True)
        embed.add_field(
            name="Progression",
            value=self._create_progress_bar(xp_in_level, xp_max),
            inline=False,
        )
        embed.set_thumbnail(url=target_user.avatar_url)
        await ctx.send(embed=embed)

    async def _get_member_info(self, guild: Guild, user_id: str) -> Optional[tuple[str, str]]:
        """Get member display name and username."""
        member = guild.get_member(user_id)
        if member is None:
            member = await self.bot.fetch_user(user_id)
        if member is None:
            return None
        return member.display_name, member.username

    def _format_leaderboard_entry(self, rank: int, display_name: str, username: str, 
                                   lvl: int, xp_in_level: int, xp_max: int, 
                                   total_xp: int, msg_count: int) -> tuple[str, str]:
        """Format a leaderboard entry."""
        rank_text = get_rank_display(rank)
        percentage = f"({(xp_in_level / xp_max * 100):.0f}"
        name = f"{rank_text} {display_name} ({username})"
        value = f"`Niveau {str(lvl).rjust(2)} {percentage.rjust(3)} %) | XP : {str(format_number(total_xp)).rjust(7)} | {str(format_number(msg_count)).rjust(5)} messages`"
        return name, value

    async def _build_leaderboard_embeds(self, guild: Guild) -> list[Embed]:
        """Build leaderboard embeds for a guild."""
        rankings = self.db[str(guild.id)].find().sort("xp", -1)
        embeds = []
        embed = create_leaderboard_embed(guild.name)
        entry_count = 0
        
        for entry in rankings:
            try:
                member_info = await self._get_member_info(guild, entry["_id"])
                if member_info is None:
                    continue
                
                display_name, username = member_info
                total_xp = entry["xp"]
                lvl, xp_in_level, xp_max = calculate_level(total_xp)
                
                name, value = self._format_leaderboard_entry(
                    entry_count, display_name, username,
                    lvl, xp_in_level, xp_max,
                    total_xp, entry["msg"]
                )
                embed.add_field(name=name, value=value, inline=False)
                entry_count += 1
                
                if entry_count % LEADERBOARD_PAGE_SIZE == 0:
                    embeds.append(embed)
                    embed = create_leaderboard_embed(guild.name, is_continuation=True)
            except KeyError:
                continue
        
        embeds.append(embed)
        return embeds

    @slash_command(
        name="leaderboard",
        description="Affiche le classement des utilisateurs",
        scopes=enabled_servers,
    )
    async def leaderboard(self, ctx: SlashContext):
        """Display the user leaderboard."""
        embeds = await self._build_leaderboard_embeds(ctx.guild)
        paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
        await paginator.send(ctx)

    @Task.create(TimeTrigger(utc=False))
    async def leaderboardpermanent(self):
        """Update the permanent leaderboard message for all enabled guilds."""
        for guild_id in enabled_servers:
            await self._update_permanent_leaderboard(guild_id)

    async def _update_permanent_leaderboard(self, guild_id: str) -> None:
        """Update the permanent leaderboard for a specific guild."""
        guild_config = module_config.get(guild_id, {})
        channel_id = guild_config.get("xpChannelId")
        message_id = guild_config.get("xpMessageId")
        
        if channel_id is None or message_id is None:
            logger.debug("No leaderboard message found for %s", guild_id)
            return
        
        try:
            guild: Guild = await self.bot.fetch_guild(guild_id)
            if guild is None:
                logger.error("Could not fetch guild %s", guild_id)
                return
            
            channel = await guild.fetch_channel(channel_id)
            if channel is None:
                logger.error("Could not fetch channel %s", channel_id)
                return
            
            message: Message = await channel.fetch_message(message_id)
            
            embeds = await self._build_leaderboard_embeds(guild)
            paginator = CustomPaginator.create_from_embeds(self.bot, *embeds)
            
            logger.debug("Updating leaderboard for %s", guild.name)
            paginator_dict = paginator.to_dict()
            await message.edit(
                content="",
                embeds=paginator_dict["embeds"],
                components=paginator_dict["components"],
            )
        except Exception as e:
            logger.error("Failed to update leaderboard for guild %s: %s", guild_id, e)


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
