"""RemindersMixin — daily /journa reminders, user-configurable reminder scheduling."""

import random
from datetime import datetime, timedelta

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    IntervalTrigger,
    Message,
    OptionType,
    SlashCommandChoice,
    SlashContext,
    Task,
    TimeTrigger,
    User,
    slash_command,
    slash_option,
)
from interactions.api.events import Component

from features.coloc.constants import (
    ADVENT_CALENDAR_REMINDERS,
    PARIS_TZ,
    ReminderType,
    get_advent_calendar_url,
    get_reminder_message,
)
from src.discord_ext.messages import fetch_user_safe

from ._common import enabled_servers, logger


class RemindersMixin:
    """Daily journa reminders (set/remove/dispatch) + advent calendar nudges."""

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
    async def set_reminder(self, ctx: SlashContext, heure: int, minute: int, type: str):
        """Set a daily reminder for /journa."""
        remind_time = datetime.now().replace(hour=heure, minute=minute, second=0, microsecond=0)
        if remind_time <= datetime.now():
            remind_time += timedelta(days=1)

        user_id = str(ctx.author.id)

        if type == "BOTH":
            self.reminders.add_reminder(remind_time, user_id, ReminderType.NORMAL)
            self.reminders.add_reminder(remind_time, user_id, ReminderType.HARDCORE)
        else:
            self.reminders.add_reminder(remind_time, user_id, ReminderType(type))

        await self.storage.save_reminders(self.reminders)

        await ctx.send(f"Rappel ajouté à {remind_time.strftime('%H:%M')}", ephemeral=True)
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
            await self.storage.save_reminders(self.reminders)

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
                    await self._process_reminder(user_id, reminder_type, current_time)

                    next_remind = remind_time + timedelta(days=1)
                    if next_remind not in reminders_to_add:
                        reminders_to_add[next_remind] = {"NORMAL": [], "HARDCORE": []}
                    reminders_to_add[next_remind][type_name].append(user_id)

            self.reminders.reminders.pop(remind_time, None)

        for next_remind, reminder_data in reminders_to_add.items():
            for type_name in ["NORMAL", "HARDCORE"]:
                for user_id in reminder_data[type_name]:
                    self.reminders.add_reminder(next_remind, user_id, ReminderType(type_name))

        await self.storage.save_reminders(self.reminders)

    async def _process_reminder(
        self,
        user_id: str,
        reminder_type: ReminderType,
        current_time: datetime,
    ) -> None:
        """Process a single reminder for a user."""
        if reminder_type == ReminderType.HARDCORE and self.event_state.hardcore_season is None:
            return

        try:
            _, user = await fetch_user_safe(self.bot, user_id)
            if not user:
                logger.warning("Could not fetch user %s", user_id)
                return
            today = current_time.strftime("%Y-%m-%d")

            journa_done = await self.api_client.check_user_journa_done(
                user.username, reminder_type, today
            )

            if not journa_done:
                message = random.choice(get_reminder_message(reminder_type))
                await user.send(message)
                logger.info(f"Sent {reminder_type.value} reminder to {user.display_name}")

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
            unopened_days = await self.api_client.get_unopened_calendar_days(user.username, day)
            if unopened_days:
                url = get_advent_calendar_url(user.username)
                message = random.choice(ADVENT_CALENDAR_REMINDERS).format(url=url)
                cases_str = ", ".join(map(str, unopened_days))
                message += f"\n\nCases manquantes : **{cases_str}**"
                await user.send(message)
                logger.info(f"Sent advent calendar reminder to {user.display_name}")
        except Exception as e:
            logger.warning(f"Error checking advent calendar for {user.display_name}: {e}")
