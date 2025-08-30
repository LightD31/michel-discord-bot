"""
Birthday Extension for Discord Bot

This extension manages user birthdays with features including:
- Adding/updating birthdays with timezone support
- Automatic birthday notifications
- Birthday role management
- Listing all birthdays with pagination
- Robust error handling and validation

Author: Improved by Assistant
Version: 2.0
"""

import os
import random
from datetime import datetime
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
    listen,
    slash_command,
    slash_option,
)
from interactions.ext import paginators

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleBirthday")


# Custom exception classes
class BirthdayError(Exception):
    """Base exception for Birthday extension errors"""
    pass


class DatabaseError(BirthdayError):
    """Exception raised when database operations fail"""
    pass


class ValidationError(BirthdayError):
    """Exception raised when data validation fails"""
    pass


class DiscordAPIError(BirthdayError):
    """Exception raised when Discord API calls fail"""
    pass


class BirthdayClass(Extension):
    def __init__(self, bot):
        self.bot: Client = bot
        # Database connection
        try:
            client = pymongo.MongoClient(config["mongodb"]["url"])
            db = client["Playlist"]
            self.collection = db["birthday"]
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise DatabaseError(f"Database connection failed: {e}")

    def _validate_and_parse_date(self, date_str: str) -> datetime:
        """Validate and parse date string to datetime object"""
        try:
            return datetime.strptime(date_str, "%d/%m/%Y")
        except ValueError:
            raise ValidationError("Date invalide. Format attendu: JJ/MM/AAAA")

    def _validate_timezone(self, timezone_str: str) -> pytz.BaseTzInfo:
        """Validate timezone string and return timezone object"""
        if timezone_str not in pytz.all_timezones:
            raise ValidationError("Fuseau horaire invalide")
        return pytz.timezone(timezone_str)

    async def _get_user_birthday(self, user_id: int, server_id: int) -> Optional[dict]:
        """Get user's birthday from database"""
        try:
            return self.collection.find_one({"user": user_id, "server": server_id})
        except Exception as e:
            logger.error(f"Failed to get user birthday: {e}")
            raise DatabaseError(f"Failed to retrieve birthday: {e}")

    async def _update_birthday(self, user_id: int, server_id: int, date: datetime, timezone: pytz.BaseTzInfo, hideyear: Optional[bool]):
        """Update existing birthday in database"""
        try:
            self.collection.update_one(
                {"user": user_id, "server": server_id},
                {
                    "$set": {
                        "date": date,
                        "timezone": timezone.zone,
                        "hideyear": hideyear or False,
                    }
                },
            )
        except Exception as e:
            logger.error(f"Failed to update birthday: {e}")
            raise DatabaseError(f"Failed to update birthday: {e}")

    async def _add_birthday(self, user_id: int, server_id: int, date: datetime, timezone: pytz.BaseTzInfo, hideyear: Optional[bool]):
        """Add new birthday to database"""
        try:
            self.collection.insert_one(
                {
                    "user": user_id,
                    "server": server_id,
                    "date": date,
                    "timezone": timezone.zone,
                    "hideyear": hideyear or False,
                    "isBirthday": False,
                }
            )
        except Exception as e:
            logger.error(f"Failed to add birthday: {e}")
            raise DatabaseError(f"Failed to add birthday: {e}")

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
        """
        Add or modify a user's birthday
        
        Args:
            ctx: The slash command context
            date: Birthday date in DD/MM/YYYY format
            timezone: Timezone string (e.g., Europe/Paris)
            hideyear: Whether to hide the birth year (optional)
        """
        if not ctx.guild:
            await ctx.send("Cette commande ne peut être utilisée que dans un serveur", ephemeral=True)
            return

        try:
            # Validate and parse date
            parsed_date = self._validate_and_parse_date(date)
            
            # Validate timezone
            validated_timezone = self._validate_timezone(timezone)
            
            # Check if user already has a birthday in this server
            existing_birthday = await self._get_user_birthday(ctx.author.id, ctx.guild.id)
            
            if existing_birthday:
                await self._update_birthday(ctx.author.id, ctx.guild.id, parsed_date, validated_timezone, hideyear)
                await ctx.send("Anniversaire mis à jour", ephemeral=True)
                logger.info(
                    "Anniversaire de %s mis à jour sur le serveur %s (%s)",
                    ctx.author.display_name,
                    ctx.guild.name,
                    parsed_date.strftime("%d/%m/%Y"),
                )
            else:
                await self._add_birthday(ctx.author.id, ctx.guild.id, parsed_date, validated_timezone, hideyear)
                await ctx.send("Anniversaire ajouté", ephemeral=True)
                logger.info(
                    "Anniversaire de %s ajouté sur le serveur %s (%s)",
                    ctx.author.display_name,
                    ctx.guild.name,
                    parsed_date.strftime("%d/%m/%Y"),
                )
                
        except ValidationError as e:
            await ctx.send(str(e), ephemeral=True)
        except DatabaseError as e:
            logger.error(f"Database error for user {ctx.author.display_name}: {e}")
            await ctx.send("Erreur lors de l'enregistrement. Veuillez réessayer.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error in anniversaire command: {e}")
            await ctx.send("Une erreur inattendue s'est produite.", ephemeral=True)

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
        """Remove user's birthday from current server"""
        if not ctx.guild:
            await ctx.send("Cette commande ne peut être utilisée que dans un serveur", ephemeral=True)
            return
            
        try:
            result = self.collection.delete_one({"user": ctx.author.id, "server": ctx.guild.id})
            if result.deleted_count > 0:
                await ctx.send("Anniversaire supprimé", ephemeral=True)
                logger.info(f"Birthday removed for {ctx.author.display_name} on server {ctx.guild.name}")
            else:
                await ctx.send("Aucun anniversaire trouvé à supprimer", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to delete birthday: {e}")
            await ctx.send("Erreur lors de la suppression", ephemeral=True)

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
        """List all birthdays on current server"""
        if not ctx.guild:
            await ctx.send("Cette commande ne peut être utilisée que dans un serveur", ephemeral=True)
            return
            
        try:
            # Get all birthdays for this server
            birthdays = list(self.collection.find({"server": ctx.guild.id}))
            
            if not birthdays:
                await ctx.send("Aucun anniversaire enregistré sur ce serveur", ephemeral=True)
                return
            
            # Get locale configuration
            locale = module_config.get(str(ctx.guild.id), {}).get("birthdayGuildLocale", "en_US")
            date_format = str(get_date_format("long", locale=locale))
            # Remove the year from the date format
            date_format = date_format.replace("y", "").strip()

            # Create embeds for pagination
            embeds = []
            embed = Embed(
                title="Anniversaires",
                description="",
                color=0x00FF00,
            )
            birthday_list = ""
            
            # Sort by date without taking the year into account
            birthdays = sorted(
                birthdays,
                key=lambda x: x["date"].replace(year=2000),
            )
            
            users_processed = 0
            for birthday in birthdays:
                try:
                    date: datetime = birthday["date"]
                    user = await self.bot.fetch_user(birthday["user"])
                    
                    if not user:
                        logger.warning(f"Could not fetch user {birthday['user']}")
                        continue
                        
                    hideyear: bool = birthday.get("hideyear", False)
                    if hideyear:
                        birthday_list += f"**{user.mention}** : {format_date(date, date_format, locale=locale)}\n"
                    else:
                        birthday_list += f"**{user.mention}** : {format_date(date, date_format, locale=locale)} ({datetime.now().year - date.year} ans)\n"
                    
                    users_processed += 1
                    
                    # Create new embed every 25 users
                    if users_processed % 25 == 0:
                        embed.description = birthday_list
                        embeds.append(embed)
                        embed = Embed(
                            title="Anniversaires",
                            description="",
                            color=0x00FF00,
                        )
                        birthday_list = ""
                        
                except Exception as e:
                    logger.error(f"Error processing birthday for user {birthday['user']}: {e}")
                    continue
            
            # Add the last embed if it has content
            if birthday_list:
                embed.description = birthday_list
                embeds.append(embed)
            
            if not embeds:
                await ctx.send("Impossible de récupérer les anniversaires", ephemeral=True)
                return
                
            # Send paginated response
            paginator = CustomPaginator.create_from_embeds(self.bot, *embeds, timeout=3600)
            await paginator.send(ctx)
            
        except Exception as e:
            logger.error(f"Error in anniversaire_liste: {e}")
            await ctx.send("Erreur lors de la récupération des anniversaires", ephemeral=True)

    @Task.create(OrTrigger(*[TimeTrigger(i, j) for i in range(24) for j in [0, 30]]))
    # @Task.create(TimeTrigger(0, 14, 10, utc=False))
    async def anniversaire_check(self):
        """Periodic task to check for birthdays and update roles"""
        logger.debug("Starting birthday check task")
        
        try:
            # Get all birthdays from database
            birthdays = list(self.collection.find())
            logger.debug(f"Found {len(birthdays)} birthdays to check")
            
            for birthday in birthdays:
                try:
                    await self._process_birthday(birthday)
                except Exception as e:
                    logger.error(f"Error processing birthday for user {birthday.get('user', 'unknown')}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Critical error in anniversaire_check task: {e}")

    async def _process_birthday(self, birthday: dict):
        """Process a single birthday entry"""
        try:
            date: datetime = birthday["date"]
            timezone = pytz.timezone(birthday["timezone"])
            
            # Get current date and time in the user's timezone
            now_in_user_tz = datetime.now(timezone)
            
            # Create the birthday datetime for this year in the user's timezone
            birthday_this_year = timezone.localize(
                datetime(now_in_user_tz.year, date.month, date.day, 0, 0, 0)
            )
            
            logger.debug(
                "Processing birthday - now in user tz: %s, birthday this year: %s",
                now_in_user_tz,
                birthday_this_year,
            )
            
            # Check if today is the birthday
            if now_in_user_tz.date() == birthday_this_year.date():
                await self._handle_birthday_celebration(birthday, now_in_user_tz, date)
            else:
                await self._handle_birthday_end(birthday)
                
        except Exception as e:
            logger.error(f"Error in _process_birthday: {e}")
            raise

    async def _handle_birthday_celebration(self, birthday: dict, now_in_user_tz: datetime, date: datetime):
        """Handle birthday celebration (send message, give role)"""
        # Check if birthday is already marked
        if birthday.get("isBirthday", False):
            logger.debug(f"Birthday already marked for user {birthday['user']}")
            return
            
        try:
            # Mark as birthday
            self.collection.update_one(
                {"user": birthday["user"], "server": birthday["server"]},
                {"$set": {"isBirthday": True}},
            )
            
            # Get server and member
            server = await self.bot.fetch_guild(birthday["server"])
            if not server:
                logger.warning(f"Could not fetch server {birthday['server']}")
                return
                
            member = await server.fetch_member(birthday["user"])
            if not member:
                logger.warning(f"Could not fetch member {birthday['user']} in server {server.name}")
                return
            
            # Get channel for birthday message
            channel_id = module_config.get(str(birthday["server"]), {}).get("birthdayChannelId")
            if channel_id:
                channel = await server.fetch_channel(channel_id)
            else:
                channel = server.system_channel
                
            if not channel:
                logger.warning(f"No valid channel found for birthday message in server {server.name}")
                return
            
            # Send birthday message
            await self._send_birthday_message(channel, member, now_in_user_tz.year - date.year, birthday["server"])
            
            # Give birthday role if configured
            await self._give_birthday_role(server, member, birthday["server"])
            
            logger.info(
                "Birthday celebration completed for %s on server %s (%s years old)",
                member.display_name,
                server.name,
                now_in_user_tz.year - date.year,
            )
            
        except Exception as e:
            logger.error(f"Error handling birthday celebration: {e}")
            raise

    async def _handle_birthday_end(self, birthday: dict):
        """Handle end of birthday (remove role)"""
        # Check if birthday is already marked as not birthday
        if not birthday.get("isBirthday", False):
            return
            
        try:
            # Mark as not birthday
            self.collection.update_one(
                {"user": birthday["user"], "server": birthday["server"]},
                {"$set": {"isBirthday": False}},
            )

            # Get server and member
            server = await self.bot.fetch_guild(birthday["server"])
            if not server:
                logger.warning(f"Could not fetch server {birthday['server']}")
                return
                
            member = await server.fetch_member(birthday["user"])
            if not member:
                logger.warning(f"Could not fetch member {birthday['user']} in server {server.name}")
                return
                
            logger.info(
                "Birthday ended for %s on server %s",
                member.display_name,
                server.name,
            )
            
            # Remove birthday role if configured
            await self._remove_birthday_role(server, member, birthday["server"])
            
        except Exception as e:
            logger.error(f"Error handling birthday end: {e}")
            raise

    async def _send_birthday_message(self, channel, member, age: int, server_id: str):
        """Send birthday message to channel"""
        try:
            # Get personalized messages
            server_config = module_config.get(server_id, {})
            messages = server_config.get("birthdayMessageList", ["Joyeux anniversaire {mention} ! 🎉"])
            weights = server_config.get("birthdayMessageWeights", [1] * len(messages))
            
            message_template = random.choices(messages, weights)[0]
            message = message_template.format(mention=member.mention, age=age)
            
            await channel.send(message)
            logger.debug(f"Birthday message sent to {member.display_name}")
            
        except Exception as e:
            logger.error(f"Error sending birthday message: {e}")
            raise

    async def _give_birthday_role(self, server, member, server_id: str):
        """Give birthday role to member"""
        try:
            role_id = module_config.get(server_id, {}).get("birthdayRoleId")
            if not role_id:
                return
                
            role = await server.fetch_role(role_id)
            if not role:
                logger.warning(f"Could not fetch birthday role {role_id} in server {server.name}")
                return
                
            await member.add_role(role)
            logger.info(
                "Birthday role %s given to %s on server %s",
                role.name,
                member.display_name,
                server.name,
            )
            
        except Exception as e:
            logger.error(f"Error giving birthday role: {e}")
            raise

    async def _remove_birthday_role(self, server, member, server_id: str):
        """Remove birthday role from member"""
        try:
            role_id = module_config.get(server_id, {}).get("birthdayRoleId")
            if not role_id:
                return
                
            role = await server.fetch_role(role_id)
            if not role:
                logger.warning(f"Could not fetch birthday role {role_id} in server {server.name}")
                return
                
            if role in member.roles:
                await member.remove_role(role)
                logger.info(
                    "Birthday role %s removed from %s on server %s",
                    role.name,
                    member.display_name,
                    server.name,
                )
                
        except Exception as e:
            logger.error(f"Error removing birthday role: {e}")
            raise


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
