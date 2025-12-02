"""
Coloc Extension - Zunivers integration for Discord.

This module provides:
- Daily /journa reminders with support for Normal and Hardcore modes
- Zunivers event tracking and notifications
- Hardcore season tracking
- Corporation daily recaps
- Advent calendar reminders (December 1-25)
"""

import os
import random
from datetime import datetime, timedelta

from typing import Optional
from datetime import date as date_type

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    Embed,
    Extension,
    GuildText,
    IntervalTrigger,
    Message,
    OptionType,
    OrTrigger,
    SlashCommandChoice,
    SlashContext,
    Task,
    TimeTrigger,
    User,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import Component

from src import logutil
from src.utils import load_config
from src.coloc.api_client import ZuniversAPIClient, ZuniversAPIError
from src.coloc.constants import (
    ADVENT_CALENDAR_REMINDERS,
    CURRENCY_EMOJI,
    DEFAULT_CORPORATION_ID,
    PARIS_TZ,
    ReminderType,
    get_advent_calendar_url,
    get_reminder_message,
    ACTION_TYPE_NAMES,
)
from src.coloc.models import (
    EventState,
    HardcoreSeason,
    ReminderCollection,
    ZuniversEvent,
)
from src.coloc.storage import StorageManager
from src.coloc.utils import (
    create_corporation_embed,
    create_corporation_logs_embed,
    create_event_embed,
    create_season_embed,
    image_url_needs_download,
    set_event_embed_image,
)

logger = logutil.init_logger(os.path.basename(__file__))

# Load configuration
config, module_config, enabled_servers = load_config("moduleColoc")
module_config = module_config[enabled_servers[0]]


class ColocExtension(Extension):
    """Extension for Zunivers-related features."""

    def __init__(self, bot: Client):
        self.bot = bot
        self.api_client = ZuniversAPIClient()
        self.storage = StorageManager(config["misc"]["dataFolder"])
        self.reminders = ReminderCollection()
        self.event_state = EventState()

    # ==================== Lifecycle ====================

    @listen()
    async def on_startup(self):
        """Initialize the extension on bot startup."""
        # Load persistent data
        self.reminders = self.storage.load_reminders()
        self.event_state = self.storage.load_event_state()
        
        # Start scheduled tasks
        self.daily_journa_check.start()
        self.reminder_checker.start()
        self.corporation_recap.start()
        self.events_checker.start()
        
        logger.info("Coloc extension started successfully")

    async def async_stop(self):
        """Clean up resources when the extension stops."""
        await self.api_client.close()

    # ==================== Fun Commands ====================

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

    # ==================== Daily Journa Check ====================

    @Task.create(TimeTrigger(22, utc=False))
    async def daily_journa_check(self):
        """Check if the daily journa message was posted and send a reminder if not."""
        channel = await self._get_zunivers_channel()
        if not channel:
            return

        now = datetime.now(PARIS_TZ)
        history = channel.history(limit=1)
        messages = await history.flatten()
        if not messages:
            return
        message: Message = messages[0]
        message_date = message.created_at.astimezone(PARIS_TZ).date()

        if message_date == now.date():
            logger.info("Daily journa already posted, skipping reminder")
            return

        await channel.send(
            ":robot: <@&934560421912911882>, heureusement que les robots n'oublient pas ! :robot:"
        )

    # ==================== Reminder Management ====================

    @slash_command(
        name="journa",
        sub_cmd_name="set",
        sub_cmd_description="Ajoute un rappel pour /journa",
        description="Gère les rappels pour /journa",
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
        name="minute",
        description="Minute du rappel",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=59,
    )
    @slash_option(
        name="type",
        description="Type de /journa",
        opt_type=OptionType.STRING,
        required=True,
        choices=[
            SlashCommandChoice(name="Normal", value="NORMAL"),
            SlashCommandChoice(name="Hardcore", value="HARDCORE"),
            SlashCommandChoice(name="Les deux", value="BOTH"),
        ],
    )
    async def set_reminder(
        self, ctx: SlashContext, heure: int, minute: int, type: str
    ):
        """Set a daily reminder for /journa."""
        remind_time = datetime.now().replace(
            hour=heure, minute=minute, second=0, microsecond=0
        )
        if remind_time <= datetime.now():
            remind_time += timedelta(days=1)

        user_id = str(ctx.author.id)

        if type == "BOTH":
            self.reminders.add_reminder(remind_time, user_id, ReminderType.NORMAL)
            self.reminders.add_reminder(remind_time, user_id, ReminderType.HARDCORE)
        else:
            self.reminders.add_reminder(remind_time, user_id, ReminderType(type))

        self.storage.save_reminders(self.reminders)

        await ctx.send(
            f"Rappel ajouté à {remind_time.strftime('%H:%M')}", ephemeral=True
        )
        logger.info(
            "Reminder %s at %s added for %s",
            type,
            remind_time.strftime("%H:%M"),
            ctx.author.display_name,
        )

    @set_reminder.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Supprime un rappel pour /journa",
    )
    async def remove_reminder(self, ctx: SlashContext):
        """Remove a /journa reminder."""
        user_id = str(ctx.user.id)
        user_reminders = self.reminders.get_user_reminders(user_id)

        if not user_reminders:
            await ctx.send("Tu n'as aucun rappel configuré.", ephemeral=True)
            return

        buttons = [
            Button(
                label=f"{remind_time.strftime('%H:%M')} - {reminder_type.value.capitalize()}",
                style=ButtonStyle.SECONDARY,
                custom_id=f"{remind_time.timestamp()}_{reminder_type.value}",
            )
            for remind_time, reminder_type in user_reminders
        ]

        components = [ActionRow(*buttons[i : i + 5]) for i in range(0, len(buttons), 5)]
        message = await ctx.send(
            "Quel rappel veux-tu supprimer ?",
            components=components,
            ephemeral=True,
        )

        try:
            button_ctx: Component = await self.bot.wait_for_component(
                components=components,
                timeout=60,
            )

            timestamp, reminder_type_str = button_ctx.ctx.custom_id.split("_")
            remind_time = datetime.fromtimestamp(float(timestamp))
            reminder_type = ReminderType(reminder_type_str)

            self.reminders.remove_reminder(remind_time, user_id, reminder_type)
            self.storage.save_reminders(self.reminders)

            await button_ctx.ctx.edit_origin(
                content=f"Rappel {reminder_type.value} à {remind_time.strftime('%H:%M')} supprimé.",
                components=[],
            )
            logger.info(
                "Reminder %s at %s removed for %s",
                reminder_type.value,
                remind_time.strftime("%H:%M"),
                ctx.user.display_name,
            )
        except TimeoutError:
            await message.edit(content="Aucun rappel sélectionné.", components=[])

    @Task.create(IntervalTrigger(minutes=1))
    async def reminder_checker(self):
        """Check and send due reminders."""
        current_time = datetime.now()
        due_reminders = self.reminders.get_due_reminders(current_time)

        if not due_reminders:
            return

        reminders_to_add: dict[datetime, dict[str, list[str]]] = {}

        for remind_time, reminder_types in due_reminders:
            for type_name in ["NORMAL", "HARDCORE"]:
                reminder_type = ReminderType(type_name)
                for user_id in reminder_types[type_name].copy():
                    await self._process_reminder(
                        user_id, reminder_type, current_time
                    )
                    
                    # Schedule next reminder
                    next_remind = remind_time + timedelta(days=1)
                    if next_remind not in reminders_to_add:
                        reminders_to_add[next_remind] = {"NORMAL": [], "HARDCORE": []}
                    reminders_to_add[next_remind][type_name].append(user_id)

            # Remove processed reminder
            self.reminders.reminders.pop(remind_time, None)

        # Add next day reminders
        for next_remind, reminder_data in reminders_to_add.items():
            for type_name in ["NORMAL", "HARDCORE"]:
                for user_id in reminder_data[type_name]:
                    self.reminders.add_reminder(
                        next_remind, user_id, ReminderType(type_name)
                    )

        self.storage.save_reminders(self.reminders)

    async def _process_reminder(
        self,
        user_id: str,
        reminder_type: ReminderType,
        current_time: datetime,
    ) -> None:
        """Process a single reminder for a user."""
        try:
            user = await self.bot.fetch_user(user_id)
            if not user:
                logger.warning(f"Could not fetch user {user_id}")
                return
            today = current_time.strftime("%Y-%m-%d")

            # Check if journa is done
            journa_done = await self.api_client.check_user_journa_done(
                user.username, reminder_type, today
            )

            if not journa_done:
                message = random.choice(get_reminder_message(reminder_type))
                await user.send(message)
                logger.info(f"Sent {reminder_type.value} reminder to {user.display_name}")

            # Check advent calendar (December 1-25, NORMAL only)
            if (
                current_time.month == 12
                and 1 <= current_time.day <= 25
                and reminder_type == ReminderType.NORMAL
            ):
                await self._check_advent_calendar(user, current_time.day)

        except Exception as e:
            if "404" not in str(e):
                logger.error(f"Error processing reminder for user {user_id}: {e}")

    async def _check_advent_calendar(self, user: User, day: int) -> None:
        """Check and send advent calendar reminder if needed."""
        try:
            calendar_opened = await self.api_client.check_user_calendar_opened(
                user.username, day
            )
            if not calendar_opened:
                url = get_advent_calendar_url(user.username)
                message = random.choice(ADVENT_CALENDAR_REMINDERS).format(url=url)
                await user.send(message)
                logger.info(f"Sent advent calendar reminder to {user.display_name}")
        except Exception as e:
            logger.warning(f"Error checking advent calendar for {user.display_name}: {e}")

    # ==================== Event Tracking ====================

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, minute=1) for i in range(24)]))
    async def events_checker(self):
        """Check for Zunivers event changes every hour."""
        await self._check_zunivers_events()

    @slash_command(
        name="zunivers",
        sub_cmd_name="check",
        sub_cmd_description="Vérifie manuellement les événements Zunivers",
        description="Gère les événements Zunivers",
        scopes=enabled_servers,
    )
    async def zunivers_check(self, ctx: SlashContext):
        """Manually trigger event check."""
        await ctx.defer()
        try:
            await self._check_zunivers_events()
            await ctx.send("Vérification des événements Zunivers terminée ! 🎉", ephemeral=True)
        except Exception as e:
            await ctx.send(f"Erreur lors de la vérification: {e}", ephemeral=True)
            logger.error(f"Manual event check failed: {e}")

    async def _check_zunivers_events(self) -> None:
        """Check current events and detect changes."""
        channel = await self._get_zunivers_channel()
        if not channel:
            return

        for rule_set in [ReminderType.NORMAL, ReminderType.HARDCORE]:
            await self._check_events_for_rule_set(channel, rule_set)

        await self._check_hardcore_season(channel)
        self.storage.save_event_state(self.event_state)

    async def _check_events_for_rule_set(
        self, channel: GuildText, rule_set: ReminderType
    ) -> None:
        """Check events for a specific rule set."""
        try:
            current_events = await self.api_client.get_current_events(rule_set)
            previous_state = self.event_state.events.get(rule_set.value, {})
            current_state = {}

            for event_data in current_events:
                event = ZuniversEvent.from_api_response(event_data)
                current_state[event.id] = event.to_state_dict()

                if event.id not in previous_state:
                    if event.is_active:
                        await self._send_event_notification(
                            channel, event_data, "start", rule_set
                        )
                        logger.info(f"New {rule_set.value} event: {event.name}")
                else:
                    prev_active = previous_state[event.id]["is_active"]
                    if prev_active != event.is_active:
                        event_type = "start" if event.is_active else "end"
                        await self._send_event_notification(
                            channel, event_data, event_type, rule_set
                        )
                        logger.info(f"{rule_set.value} event {event_type}: {event.name}")

            # Check for disappeared events
            for event_id, prev_event in previous_state.items():
                if event_id not in current_state and prev_event["is_active"]:
                    fake_event = {
                        "id": event_id,
                        "name": prev_event["name"],
                        "isActive": False,
                        "beginDate": prev_event["begin_date"],
                        "endDate": prev_event["end_date"],
                    }
                    await self._send_event_notification(
                        channel, fake_event, "end", rule_set
                    )
                    logger.info(f"{rule_set.value} event ended (disappeared): {prev_event['name']}")

            self.event_state.events[rule_set.value] = current_state

        except Exception as e:
            logger.error(f"Error checking {rule_set.value} events: {e}")

    async def _send_event_notification(
        self,
        channel: GuildText,
        event: dict,
        event_type: str,
        rule_set: ReminderType,
    ) -> None:
        """Send an event notification embed to the channel."""
        embed = create_event_embed(event, event_type, rule_set)
        
        image_file = None
        image_url = event.get("imageUrl")
        if image_url and image_url_needs_download(image_url):
            image_file = await self.api_client.download_image(image_url, "event_image.webp")
        
        set_event_embed_image(embed, image_url, image_file)
        
        if image_file:
            await channel.send(embed=embed, file=image_file)
        else:
            await channel.send(embed=embed)

    async def _check_hardcore_season(self, channel: GuildText) -> None:
        """Check for hardcore season changes."""
        try:
            current_data = await self.api_client.get_current_hardcore_season()
            current_season = HardcoreSeason.from_api_response(current_data) if current_data else None
            previous_season = self.event_state.hardcore_season

            if previous_season is None and current_season is not None and current_data:
                embed = create_season_embed(current_data, "start")
                await channel.send(embed=embed)
                logger.info(f"New hardcore season: Season {current_season.index}")

            elif previous_season is not None and current_season is None:
                embed = create_season_embed(previous_season.to_dict(), "end")
                await channel.send(embed=embed)
                logger.info(f"Hardcore season ended: Season {previous_season.index}")

            elif (
                previous_season is not None
                and current_season is not None
                and current_data is not None
                and previous_season.id != current_season.id
            ):
                embed_end = create_season_embed(previous_season.to_dict(), "end")
                embed_start = create_season_embed(current_data, "start")
                await channel.send(embeds=[embed_end, embed_start])
                logger.info(
                    f"Season change: Season {previous_season.index} → {current_season.index}"
                )

            self.event_state.hardcore_season = current_season

        except Exception as e:
            logger.error(f"Error checking hardcore season: {e}")

    # ==================== Corporation Recap ====================

    @Task.create(TimeTrigger(23, 59, 45, utc=False))
    async def corporation_recap(self, date: Optional[str] = None):
        """Send daily corporation recap."""
        channel = await self._get_zunivers_channel()
        if not channel:
            return

        try:
            data = await self.api_client.get_corporation(DEFAULT_CORPORATION_ID)
            if not data:
                logger.warning("Could not fetch corporation data")
                return
        except ZuniversAPIError as e:
            await channel.send(f"Erreur lors de la récupération des données: {e}")
            return

        target_date = self._parse_date(date)
        if target_date is None:
            return

        logs = self._process_corporation_logs(data.get("corporationLogs", []), target_date)
        
        if not logs:
            return

        all_members = {
            m["user"]["discordGlobalName"] for m in data.get("userCorporations", [])
        }

        corp_embed = create_corporation_embed(data, CURRENCY_EMOJI)
        logs_embed = create_corporation_logs_embed(logs, all_members, target_date, CURRENCY_EMOJI)

        await channel.send(embeds=[corp_embed, logs_embed])

    @slash_command(
        name="corpo",
        description="Affiche les informations de la corporation",
        scopes=[668445729928249344],
    )
    @slash_option(
        name="date",
        description="Date du récap (YYYY-MM-DD)",
        opt_type=OptionType.STRING,
        required=False,
    )
    async def corpo_command(self, ctx: SlashContext, date: Optional[str] = None):
        """Manual corporation recap command."""
        await self.corporation_recap(date=date)
        await ctx.send("Corporation recap envoyé !", ephemeral=True)

    def _parse_date(self, date_str: Optional[str] = None) -> Optional[date_type]:
        """Parse a date string or return today's date."""
        if date_str is None:
            return datetime.today().date()
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Invalid date format: {date_str}")
            return None

    def _process_corporation_logs(
        self, logs: list[dict], target_date: date_type
    ) -> list[dict]:
        """Filter and process corporation logs for a specific date."""
        today_logs = []
        for log in logs:
            log_date = datetime.strptime(log["date"], "%Y-%m-%dT%H:%M:%S.%f").date()
            if log_date == target_date:
                today_logs.append(log)

        today_logs.sort(key=lambda x: datetime.strptime(x["date"], "%Y-%m-%dT%H:%M:%S.%f"))

        # Merge logs with same timestamp (for upgrades)
        merged = []
        i = 0
        while i < len(today_logs):
            log = today_logs[i]
            merged_log = {
                "user_name": log["user"]["discordGlobalName"],
                "date": log["date"],
                "action": ACTION_TYPE_NAMES.get(log["action"], log["action"]),
                "amount": log.get("amount", 0),
            }

            if log["action"] == "UPGRADE":
                j = i + 1
                while j < len(today_logs) and today_logs[j]["date"] == log["date"]:
                    merged_log["amount"] += today_logs[j].get("amount", 0)
                    j += 1
                i = j
            else:
                i += 1

            merged.append(merged_log)

        return merged

    # ==================== Helpers ====================

    async def _get_zunivers_channel(self) -> Optional[GuildText]:
        """Get the configured Zunivers channel."""
        try:
            channel = await self.bot.fetch_channel(module_config["colocZuniversChannelId"])
            if isinstance(channel, GuildText):
                return channel
            return None
        except Exception as e:
            logger.error(f"Could not fetch Zunivers channel: {e}")
            return None
