"""Reminders Discord extension — /reminder slash command and due-check task.

Slash commands:
- ``/reminder set`` — create a reminder (optional recurrence: daily/weekly/monthly/yearly)
- ``/reminder remove`` — interactive button list to delete one of your reminders

The background ``check_reminders`` task polls every minute, DMs the author,
and either reschedules recurring reminders or deletes one-shot reminders.
Persistence lives in :class:`features.reminders.ReminderRepository` (MongoDB).
Enabled per-guild via ``moduleUtils``.
"""

import os
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    Extension,
    IntervalTrigger,
    OptionType,
    SlashCommandChoice,
    SlashContext,
    Task,
    TimestampStyles,
    listen,
    slash_command,
    slash_option,
)
from interactions.client.utils import timestamp_converter

from features.reminders import Reminder, ReminderRepository
from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.messages import fetch_user_safe, send_error

logger = logutil.init_logger(os.path.basename(__file__))
_, _, enabled_servers = load_config("moduleUtils")
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore[misc]


class RemindersExtension(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._reminder_repos: dict[str, ReminderRepository] = {}

    @listen()
    async def on_startup(self):
        for guild_id in enabled_servers:
            await self._reminder_repo(guild_id).ensure_indexes()
        self.check_reminders.start()

    def _reminder_repo(self, guild_id: str | int) -> ReminderRepository:
        gid = str(guild_id)
        repo = self._reminder_repos.get(gid)
        if repo is None:
            repo = ReminderRepository(gid)
            self._reminder_repos[gid] = repo
        return repo

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
            buttons.append(Button(label=label, style=ButtonStyle.SECONDARY, custom_id=reminder.id))
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
                        logger.info("Reminder sent to %s: %s", user.display_name, reminder.message)
                except Exception as e:
                    logger.warning("Failed to send reminder to user %s: %s", reminder.user_id, e)
                    continue

                next_time = self._next_occurrence(reminder.remind_time, reminder.frequency)
                try:
                    if next_time is not None:
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


def setup(bot: Client) -> None:
    RemindersExtension(bot)


__all__ = ["RemindersExtension", "setup"]
