"""Reminders Discord extension — /reminder slash command and due-check task.

Slash commands:
- ``/reminder set`` — create a reminder for yourself (optional recurrence)
- ``/reminder remove`` — interactive button list to delete one of your reminders

The background ``check_reminders`` task polls every minute, moves each due
reminder to its next occurrence (or deletes a one-shot one) and only then DMs
its author, with a "Snooze 10 min" button, so a reminder is never sent twice. Persistence lives in
:class:`features.reminders.ReminderRepository` (MongoDB). Enabled per-guild via
``moduleUtils``.
"""

import re
from datetime import datetime, timedelta

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    Extension,
    IntervalTrigger,
    OptionType,
    SlashCommandChoice,
    SlashContext,
    Task,
    TimestampStyles,
    component_callback,
    listen,
    slash_command,
    slash_option,
    spread_to_rows,
)
from interactions.client.utils import timestamp_converter

from features.reminders import (
    PastReminderError,
    Reminder,
    ReminderRepository,
    first_occurrence,
    next_after,
    schedule_label,
    snooze_minutes,
)
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.messages import fetch_user_safe, send_error
from src.discord_ext.scopes import guild_scopes

SNOOZE_PREFIX = "reminder_snooze"
# Per member and server; /reminder remove lists up to 25 buttons too.
MAX_REMINDERS_PER_MEMBER = 25
# A DM is capped at 2000 characters; snoozes prefix the text.
MAX_REMINDER_LENGTH = 1500
_SNOOZE_RE = re.compile(rf"^{SNOOZE_PREFIX}:(\d+):([0-9a-fA-F]+)$")

logger = logutil.init_logger(__name__)
UTILS = ModuleConfig("moduleUtils")


def _snooze_minutes(guild_id: str | int) -> int:
    return snooze_minutes(UTILS.get(guild_id))


class RemindersExtension(Extension):
    MODULE_KEYS = ("moduleUtils",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self._reminder_repos: dict[str, ReminderRepository] = {}

    @listen()
    async def on_startup(self):
        for guild_id in UTILS.guild_ids():
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
        scopes=guild_scopes(UTILS.guild_ids()),
    )
    @slash_option(
        name="tache",
        description="Tâche à rappeler",
        opt_type=OptionType.STRING,
        required=True,
        max_length=MAX_REMINDER_LENGTH,
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
        start = None
        if date:
            try:
                start = datetime.strptime(date, "%d/%m/%Y").date()
            except ValueError:
                await send_error(ctx, "Format de date invalide. Utilisez JJ/MM/AAAA")
                return
        try:
            remind_time = first_occurrence(
                datetime.now(),
                hour=hour,
                minute=minute,
                frequency=frequency,  # type: ignore[arg-type]
                start=start,
            )
        except PastReminderError as e:
            await send_error(ctx, str(e))
            return

        repo = self._reminder_repo(str(ctx.guild_id))
        if await repo.count_for_user(str(ctx.author.id)) >= MAX_REMINDERS_PER_MEMBER:
            await send_error(
                ctx,
                f"Tu as déjà {MAX_REMINDERS_PER_MEMBER} rappels sur ce serveur : "
                "supprimes-en un avec `/reminder remove`.",
            )
            return

        reminder = Reminder(
            user_id=str(ctx.author.id),
            message=task,
            remind_time=remind_time,
            frequency=frequency,  # type: ignore[arg-type]
        )
        await repo.add(reminder)

        await ctx.send(
            f"Rappel {frequency or ''} créé à {remind_time.strftime('%H:%M')}"
            f" avec le message: {task}",
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
            # Discord caps a button label at 80 characters.
            label = f"{reminder.message[:40]} ({schedule_label(reminder)})"
            buttons.append(Button(label=label, style=ButtonStyle.SECONDARY, custom_id=reminder.id))
            id_map[reminder.id] = reminder

        message = await ctx.send(
            f"Quel rappel veux-tu supprimer (annulation {timestamp_converter(datetime.now() + timedelta(seconds=60)).format(TimestampStyles.RelativeTime)})",
            # Five buttons per row, 25 per message.
            components=spread_to_rows(*buttons[:25]),
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
    def _snooze_components(reminder_id: str, guild_id: str, snooze_minutes: int) -> list[ActionRow]:
        return [
            ActionRow(
                Button(
                    label=f"Snooze {snooze_minutes} min",
                    style=ButtonStyle.SECONDARY,
                    custom_id=f"{SNOOZE_PREFIX}:{guild_id}:{reminder_id}",
                )
            )
        ]

    @component_callback(_SNOOZE_RE)
    async def on_snooze(self, ctx: ComponentContext):
        """Re-fire the reminder for the clicker after the per-guild snooze delay."""
        match = _SNOOZE_RE.match(ctx.custom_id)
        if not match:
            await ctx.send("Bouton invalide.", ephemeral=True)
            return
        gid, reminder_id = match.group(1), match.group(2)
        minutes = _snooze_minutes(gid)

        original_message = ctx.message.content if ctx.message and ctx.message.content else "Rappel"
        new_time = datetime.now() + timedelta(minutes=minutes)
        snoozed = Reminder(
            user_id=str(ctx.author.id),
            message=f"⏰ (snooze) {original_message}",
            remind_time=new_time,
        )
        try:
            await self._reminder_repo(gid).add(snoozed)
        except Exception as e:
            logger.error("Failed to snooze reminder %s: %s", reminder_id, e)
            await ctx.send("Impossible de reporter le rappel.", ephemeral=True)
            return

        await ctx.send(
            f"Rappel reporté de {minutes} minutes "
            f"({timestamp_converter(new_time).format(TimestampStyles.RelativeTime)}).",
            ephemeral=True,
        )

    @Task.create(IntervalTrigger(minutes=1))
    async def check_reminders(self):
        now = datetime.now()
        for gid in UTILS.guild_ids():
            repo = self._reminder_repo(gid)
            try:
                due = await repo.list_due(now)
            except Exception as e:
                logger.error("Failed to fetch due reminders for guild %s: %s", gid, e)
                continue

            for reminder in due:
                if reminder.id is None:
                    continue
                next_time = next_after(reminder.remind_time, reminder.frequency, now)
                try:
                    claimed = await repo.claim_due(reminder, next_time)
                except Exception as e:
                    logger.error(
                        "Failed to update reminder %s in guild %s: %s", reminder.id, gid, e
                    )
                    continue
                if claimed:
                    await self._dispatch_reminder(gid, reminder)

    async def _dispatch_reminder(self, gid: str, reminder: Reminder) -> None:
        """DM the reminder to its author with a snooze button."""
        components = self._snooze_components(reminder.id or "", gid, _snooze_minutes(gid))
        try:
            _, user = await fetch_user_safe(self.bot, reminder.user_id)
            if user:
                await user.send(reminder.message, components=components)
                logger.info("Reminder sent to %s: %s", user.display_name, reminder.message)
        except Exception as e:
            logger.warning("Failed to send reminder to user %s: %s", reminder.user_id, e)


def setup(bot: Client) -> None:
    RemindersExtension(bot)


__all__ = ["RemindersExtension", "setup"]
