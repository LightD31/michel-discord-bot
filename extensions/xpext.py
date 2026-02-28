import os
import random
import time
from datetime import datetime
from typing import Any, Optional, Dict, Tuple

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
from src.utils import CustomPaginator

from src import logutil
from src.mongodb import mongo_manager
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

# Cache settings
USER_CACHE_TTL = 300  # 5 minutes
RANK_CACHE_TTL = 60  # 1 minute


class TTLCache:
    """Simple TTL cache implementation."""
    
    def __init__(self, ttl: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._ttl = ttl
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache if it exists and is not expired."""
        if key in self._cache:
            value, timestamp = self._cache[key]
            if time.time() - timestamp < self._ttl:
                return value
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any) -> None:
        """Set a value in cache with current timestamp."""
        self._cache[key] = (value, time.time())
    
    def delete(self, key: str) -> None:
        """Delete a key from cache."""
        if key in self._cache:
            del self._cache[key]
    
    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()
    
    def cleanup(self) -> None:
        """Remove expired entries from cache."""
        current_time = time.time()
        expired_keys = [
            key for key, (_, timestamp) in self._cache.items()
            if current_time - timestamp >= self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]


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
        
        # MongoDB connection via global motor manager (per-guild DB)
        self._db_connected = True
        logger.info("XP module using global motor MongoDB manager (per-guild databases)")
        
        # Initialize caches
        self._user_cache = TTLCache(ttl=USER_CACHE_TTL)
        self._rank_cache = TTLCache(ttl=RANK_CACHE_TTL)
        
        # Validate configuration
        self._validate_config()
        
        logger.debug("enabled_servers for XP module: %s", enabled_servers)

    def _validate_config(self) -> None:
        """Validate module configuration and log warnings for missing settings."""
        for guild_id in enabled_servers:
            guild_config = module_config.get(guild_id, {})
            
            if not guild_config:
                logger.warning("No configuration found for guild %s", guild_id)
                continue
            
            # Check for optional but recommended settings
            if "xpChannelId" not in guild_config:
                logger.debug("No permanent leaderboard channel configured for guild %s", guild_id)
            
            if "levelUpMessageList" not in guild_config:
                logger.debug("Using default level up message for guild %s", guild_id)

    async def _ensure_indexes(self, guild_id: str) -> None:
        """Ensure MongoDB indexes exist for the guild collection."""
        if not self._db_connected:
            return
        
        try:
            collection = mongo_manager.get_guild_collection(guild_id, "xp")
            # Create index on xp field for faster sorting (descending for leaderboard)
            await collection.create_index([("xp", pymongo.DESCENDING)], background=True)
            # Create index on time field for cooldown checks
            await collection.create_index([("time", pymongo.DESCENDING)], background=True)
            logger.debug("Ensured indexes for guild %s", guild_id)
        except Exception as e:
            logger.error("Failed to create indexes for guild %s: %s", guild_id, e)

    @listen()
    async def on_startup(self):
        # Test connection
        try:
            connected = await mongo_manager.ping()
            if not connected:
                logger.error("MongoDB connection failed at startup, XP module disabled.")
                self._db_connected = False
        except Exception as e:
            logger.error("MongoDB ping failed: %s", e)
            self._db_connected = False

        # Ensure indexes for all enabled servers
        for guild_id in enabled_servers:
            await self._ensure_indexes(guild_id)
        
        # Start cache cleanup task
        self._cache_cleanup_task.start()
        
        self.leaderboardpermanent.start()
        await self.leaderboardpermanent()

    @Task.create(TimeTrigger(minute=30, utc=False))
    async def _cache_cleanup_task(self):
        """Periodically clean up expired cache entries."""
        self._user_cache.cleanup()
        self._rank_cache.cleanup()
        logger.debug("Cache cleanup completed")

    @listen()
    async def on_message(self, event: MessageCreate):
        """Give XP to a user when they send a message."""
        message = event.message
        
        if not self._db_connected:
            return
        
        if not self._is_valid_xp_message(message):
            return

        user_id = str(message.author.id)
        guild_id = str(message.guild.id)
        
        await self._ensure_guild_collection(guild_id, message.guild.name)
        
        try:
            stats = await mongo_manager.get_guild_collection(guild_id, "xp").find_one({"_id": user_id})
        except pymongo.errors.PyMongoError as e:
            logger.error("Database error when fetching user stats: %s", e)
            return
        
        if stats is None:
            if not await self._create_new_user(guild_id, user_id, message.created_at.timestamp()):
                return
            return
        
        # Safely get time with fallback
        last_time = stats.get("time", 0)
        if self._is_on_cooldown(message.created_at.timestamp(), last_time):
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

    async def _ensure_guild_collection(self, guild_id: str, guild_name: str) -> None:
        """Create the xp collection for the guild if it doesn't exist."""
        try:
            guild_db = mongo_manager.get_guild_db(guild_id)
            existing = await guild_db.list_collection_names()
            if "xp" not in existing:
                await guild_db.create_collection("xp")
                await self._ensure_indexes(guild_id)
                logger.debug("Created xp collection for %s.", guild_name)
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to create collection for %s: %s", guild_name, e)

    async def _create_new_user(self, guild_id: str, user_id: str, timestamp: float) -> bool:
        """Create a new user entry in the database.
        
        Returns:
            True if user was created successfully, False otherwise.
        """
        new_user = {
            "_id": user_id,
            "xp": random.randint(XP_MIN, XP_MAX),
            "time": timestamp,
            "msg": 1,
            "lvl": 0,
        }
        try:
            await mongo_manager.get_guild_collection(guild_id, "xp").insert_one(new_user)
            # Invalidate rank cache for this guild
            self._invalidate_rank_cache(guild_id)
            logger.debug("Added %s to the database.", user_id)
            return True
        except pymongo.errors.DuplicateKeyError:
            logger.debug("User %s already exists (race condition).", user_id)
            return True
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to create user %s: %s", user_id, e)
            return False

    def _invalidate_rank_cache(self, guild_id: str) -> None:
        """Invalidate rank cache entries for a guild."""
        # Clear all rank cache entries for this guild
        keys_to_delete = [
            key for key in list(self._rank_cache._cache.keys())
            if key.startswith(f"{guild_id}_")
        ]
        for key in keys_to_delete:
            self._rank_cache.delete(key)

    def _is_on_cooldown(self, current_time: float, last_time: float) -> bool:
        """Check if the user is on XP cooldown."""
        return current_time - last_time < XP_COOLDOWN_SECONDS

    async def _update_user_xp(self, guild_id: str, user_id: str, stats: dict, message: Message) -> None:
        """Update user XP and handle level ups."""
        xp_gained = random.randint(XP_MIN, XP_MAX)
        current_xp = stats.get("xp", 0)
        current_msg = stats.get("msg", 0)
        new_xp = current_xp + xp_gained
        new_msg_count = current_msg + 1
        
        try:
            await mongo_manager.get_guild_collection(guild_id, "xp").update_one(
                {"_id": user_id},
                {"$set": {
                    "xp": new_xp,
                    "time": message.created_at.timestamp(),
                    "msg": new_msg_count,
                }}
            )
            # Invalidate rank cache since XP changed
            self._invalidate_rank_cache(guild_id)
            logger.debug("Gave %s XP.", user_id)
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to update XP for %s: %s", user_id, e)
            return
        
        new_level, _, _ = calculate_level(new_xp)
        old_level = stats.get("lvl", 0)
        
        if new_level > old_level:
            await self._handle_level_up(guild_id, user_id, new_level, message)

    async def _handle_level_up(self, guild_id: str, user_id: str, new_level: int, message: Message) -> None:
        """Handle user level up notification."""
        await mongo_manager.get_guild_collection(guild_id, "xp").update_one(
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

    async def _get_user_rank(self, guild_id: str, user_id: str) -> Optional[int]:
        """Get the rank of a user in the guild using MongoDB aggregation.
        
        Returns:
            The user's rank (1-indexed), or None if user not found.
        """
        if not self._db_connected:
            return None
        
        # Check cache first
        cache_key = f"{guild_id}_{user_id}"
        cached_rank = self._rank_cache.get(cache_key)
        if cached_rank is not None:
            return cached_rank
        
        try:
            # Use aggregation with $setWindowFields for efficient rank calculation
            pipeline = [
                {
                    "$setWindowFields": {
                        "sortBy": {"xp": -1},
                        "output": {
                            "rank": {"$rank": {}}
                        }
                    }
                },
                {
                    "$match": {"_id": user_id}
                },
                {
                    "$project": {"rank": 1}
                }
            ]
            
            result = await mongo_manager.get_guild_collection(guild_id, "xp").aggregate(pipeline).to_list(length=None)
            
            if result:
                rank = result[0]["rank"]
                self._rank_cache.set(cache_key, rank)
                return rank
            
            return None
        except pymongo.errors.PyMongoError as e:
            logger.error("Failed to get rank for user %s in guild %s: %s", user_id, guild_id, e)
            # Fallback to old method if aggregation fails
            return await self._get_user_rank_fallback(guild_id, user_id)

    async def _get_user_rank_fallback(self, guild_id: str, user_id: str) -> Optional[int]:
        """Fallback method to get user rank without aggregation."""
        try:
            rankings = mongo_manager.get_guild_collection(guild_id, "xp").find({}, {"_id": 1}).sort("xp", -1)
            rank = 0
            async for entry in rankings:
                rank += 1
                if entry["_id"] == user_id:
                    return rank
            return None
        except pymongo.errors.PyMongoError as e:
            logger.error("Fallback rank query failed: %s", e)
            return None

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
        if not self._db_connected:
            await ctx.send("❌ La base de données n'est pas disponible.", ephemeral=True)
            return
        
        target_user = utilisateur or ctx.author
        guild_id = str(ctx.guild.id)
        
        try:
            stats = await mongo_manager.get_guild_collection(guild_id, "xp").find_one({"_id": str(target_user.id)})
        except pymongo.errors.PyMongoError as e:
            logger.error("Database error in rank command: %s", e)
            await ctx.send("❌ Erreur lors de la récupération des statistiques.", ephemeral=True)
            return
        
        if stats is None:
            await ctx.send(f"{target_user.mention} n'a pas encore de niveau.")
            return
        
        xp = stats.get("xp", 0)
        lvl, xp_in_level, xp_max = calculate_level(xp)
        rank = await self._get_user_rank(guild_id, str(target_user.id))
        rank_display = f"{rank}/{ctx.guild.member_count}" if rank else "N/A"
        
        embed = Embed(
            title=f"Statistiques de {target_user.username}",
            color=EMBED_COLOR,
            timestamp=datetime.now(TIMEZONE),
        )
        embed.add_field(name="Name", value=target_user.mention, inline=True)
        embed.add_field(name="Niveau", value=lvl, inline=True)
        embed.add_field(name="Rank", value=rank_display, inline=True)
        embed.add_field(name="XP", value=f"{xp_in_level}/{xp_max}", inline=True)
        embed.add_field(name="Messages", value=stats.get("msg", 0), inline=True)
        embed.add_field(
            name="Progression",
            value=self._create_progress_bar(xp_in_level, xp_max),
            inline=False,
        )
        embed.set_thumbnail(url=target_user.avatar_url)
        await ctx.send(embed=embed)

    async def _get_member_info(self, guild: Guild, user_id: str) -> Optional[tuple[str, str]]:
        """Get member display name and username with caching."""
        # Check cache first
        cache_key = f"user_{user_id}"
        cached_info = self._user_cache.get(cache_key)
        if cached_info is not None:
            return cached_info
        
        try:
            member = guild.get_member(user_id)
            if member is None:
                member = await self.bot.fetch_user(user_id)
            if member is None:
                return None
            
            result = (member.display_name, member.username)
            self._user_cache.set(cache_key, result)
            return result
        except Exception as e:
            logger.debug("Could not fetch member %s: %s", user_id, e)
            return None

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
        if not self._db_connected:
            return [Embed(
                title="Erreur",
                description="La base de données n'est pas disponible.",
                color=0xFF0000,
            )]
        
        try:
            rankings_cursor = mongo_manager.get_guild_collection(str(guild.id), "xp").find().sort("xp", -1)
            rankings = await rankings_cursor.to_list(length=None)
        except pymongo.errors.PyMongoError as e:
            logger.error("Database error building leaderboard: %s", e)
            return [Embed(
                title="Erreur",
                description="Impossible de récupérer le classement.",
                color=0xFF0000,
            )]
        
        embeds = []
        embed = create_leaderboard_embed(guild.name)
        entry_count = 0
        
        for entry in rankings:
            try:
                member_info = await self._get_member_info(guild, entry["_id"])
                if member_info is None:
                    continue
                
                display_name, username = member_info
                total_xp = entry.get("xp", 0)
                lvl, xp_in_level, xp_max = calculate_level(total_xp)
                
                name, value = self._format_leaderboard_entry(
                    entry_count, display_name, username,
                    lvl, xp_in_level, xp_max,
                    total_xp, entry.get("msg", 0)
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
