"""Reminders Discord extension — /reminder slash command and due-check task.

Slash commands:
- ``/reminder set`` — create a reminder for yourself (optional recurrence)
- ``/reminder list`` — your reminders on this server and when they next fire
- ``/reminder remove`` — interactive button list to delete one of your reminders

Right-click a message → Applications → "⏰ Me le rappeler": a form asks when
("dans 2h", "demain 18h30", :mod:`features.reminders.when`) and for an
optional note; the DM links back to the message.

The background ``check_reminders`` task polls every minute, moves each due
reminder to its next occurrence (or deletes a one-shot one) and only then DMs
its author, with a "Snooze 10 min" button, so a reminder is never sent twice. Persistence lives in
:class:`features.reminders.ReminderRepository` (MongoDB). Enabled per-guild via
``moduleUtils``.
"""

import re
from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    ContextMenuContext,
    Embed,
    Extension,
    IntervalTrigger,
    Modal,
    ModalContext,
    OptionType,
    ParagraphText,
    ShortText,
    SlashCommandChoice,
    SlashContext,
    Task,
    TimestampStyles,
    component_callback,
    listen,
    message_context_menu,
    modal_callback,
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
    snoozed_text,
)
from features.reminders.when import EXAMPLES, parse_when
from features.secretsanta.dates import parse_exchange_date
from src.core import clock
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.logging import log_failure
from src.core.text import EMBED_DESCRIPTION_LIMIT, clip, format_discord_timestamp
from src.discord_ext.embeds import Colors
from src.discord_ext.menus import menu_help
from src.discord_ext.messages import fetch_user_safe, guild_id_of, jump_url, send_error
from src.discord_ext.scopes import guild_scopes

SNOOZE_PREFIX = "reminder_snooze"
# Per member and server; /reminder remove lists up to 25 buttons too.
MAX_REMINDERS_PER_MEMBER = 25
# A DM is capped at 2000 characters; snoozes prefix the text.
MAX_REMINDER_LENGTH = 1500
# How much of each reminder's text /reminder list shows.
LIST_PREVIEW_LENGTH = 120
_SNOOZE_RE = re.compile(rf"^{SNOOZE_PREFIX}:(\d+):([0-9a-fA-F]+)$")
REMIND_MENU = "⏰ Me le rappeler"
REMIND_FORM = "remind_msg"  # remind_msg:{channel_id}:{message_id}
_REMIND_FORM_RE = re.compile(rf"^{REMIND_FORM}:(\d+):(\d+)$")
MAX_NOTE_LENGTH = 500
NO_REMINDER = (
    "Tu n'as aucun rappel sur ce serveur. Crée-en un avec `/reminder set`, ou d'un clic "
    "droit sur un message → Applications → ⏰ Me le rappeler."
)
DEFAULT_NOTE = "Revoir ce message"

logger = logutil.init_logger(__name__)
UTILS = ModuleConfig("moduleUtils")


def _snooze_minutes(guild_id: str | int) -> int:
    return snooze_minutes(UTILS.get(guild_id))


def start_day(text: str, today: date_type) -> date_type:
    """The start date of ``/reminder set``: "24/12/2026", or "24/12", "24 décembre".

    A full date may be in the past (a recurring reminder then starts at its
    next occurrence); a date without a year is the next such day.
    """
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date()
    except ValueError:
        pass
    try:
        return parse_exchange_date(text, today)
    except ValueError:
        raise ValueError(f"Je n'ai pas compris « {text} ». Exemple : 24/12/2026.") from None


def reminder_text(reminder: Reminder) -> str:
    """What the DM says: the reminder, and the message it was made from."""
    if reminder.jump_url:
        return f"{reminder.message}\n🔗 {reminder.jump_url}"
    return reminder.message


def remind_form(channel_id: Any, message_id: Any) -> Modal:
    return Modal(
        ShortText(
            label="Quand ?",
            custom_id="when",
            placeholder=EXAMPLES,
            required=True,
            max_length=60,
        ),
        ParagraphText(
            label="Une note (facultatif)",
            custom_id="note",
            placeholder="Ce que tu veux te rappeler à propos de ce message",
            required=False,
            max_length=MAX_NOTE_LENGTH,
        ),
        title="Me rappeler ce message",
        custom_id=f"{REMIND_FORM}:{channel_id}:{message_id}",
    )


def reminders_embed(reminders: list[Reminder]) -> Embed:
    """A member's reminders: what each says, its schedule and when it next fires."""
    lines = [
        f"**{schedule_label(r)}** — prochain {format_discord_timestamp(r.remind_time, 'R')}\n"
        f"{clip(r.message, LIST_PREVIEW_LENGTH)}"
        + (f" ([message]({r.jump_url}))" if r.jump_url else "")
        for r in reminders
    ]
    return Embed(
        title=f"Tes rappels ({len(reminders)}/{MAX_REMINDERS_PER_MEMBER})",
        description=clip("\n\n".join(lines), EMBED_DESCRIPTION_LIMIT),
        color=Colors.UTIL,
    )


class RemindersExtension(Extension):
    MODULE_KEYS = ("moduleUtils",)

    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        for guild_id in UTILS.guild_ids():
            await self._reminder_repo(guild_id).ensure_indexes()
        if not self.check_reminders.running:
            self.check_reminders.start()

    def _reminder_repo(self, guild_id: str | int) -> ReminderRepository:
        return ReminderRepository(str(guild_id))

    @slash_command(
        name="reminder",
        sub_cmd_name="set",
        description="Gère tes rappels",
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
        "Date de début, ex. 24/12 ou 24/12/2026 (défaut : aujourd'hui)",
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
                start = start_day(date, clock.to_local(clock.now()).date())
            except ValueError as e:
                await send_error(ctx, str(e), icon="📅")
                return
        try:
            remind_time = first_occurrence(
                clock.now(),
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
            f"⏰ Rappel créé : **{schedule_label(reminder)}**\n{task}",
            ephemeral=True,
        )
        logger.info(
            "Reminder set for %s at %s with message %s (%s)",
            ctx.author.display_name,
            clock.to_local(remind_time).strftime("%H:%M"),
            task,
            frequency,
        )

    @reminder_set.subcommand(
        sub_cmd_name="list",
        sub_cmd_description="Affiche tes rappels sur ce serveur",
    )
    async def list_reminders(self, ctx: SlashContext):
        reminders = await self._reminder_repo(guild_id_of(ctx)).list_for_user(str(ctx.author.id))
        if not reminders:
            await ctx.send(f"⏰ {NO_REMINDER}", ephemeral=True)
            return
        await ctx.send(embeds=[reminders_embed(reminders)], ephemeral=True)

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
            await send_error(ctx, NO_REMINDER, icon="⏰")
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
            f"Quel rappel veux-tu supprimer (annulation {timestamp_converter(clock.now() + timedelta(seconds=60)).format(TimestampStyles.RelativeTime)})",
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
                        content=f"Rappel à {clock.to_local(selected.remind_time).strftime('%H:%M')} supprimé.",
                        components=[],
                    )
                    logger.info(
                        "Reminder at %s deleted for %s",
                        clock.to_local(selected.remind_time).strftime("%H:%M"),
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

    @menu_help("Recevoir ce message en MP plus tard (« dans 2h », « demain 18h30 »)")
    @message_context_menu(name=REMIND_MENU, scopes=guild_scopes(UTILS.guild_ids()))
    async def remind_me_menu(self, ctx: ContextMenuContext) -> None:
        repo = self._reminder_repo(guild_id_of(ctx))
        if await repo.count_for_user(str(ctx.author.id)) >= MAX_REMINDERS_PER_MEMBER:
            await send_error(ctx, self._too_many())
            return
        message: Any = ctx.target
        await ctx.send_modal(remind_form(ctx.channel_id, message.id))

    @modal_callback(_REMIND_FORM_RE)
    async def on_remind_form(self, ctx: ModalContext, when: str = "", note: str = "") -> None:
        match = _REMIND_FORM_RE.match(ctx.custom_id)
        if not match:
            return
        channel_id, message_id = match.groups()
        try:
            remind_time = parse_when(when, clock.now())
        except ValueError as e:
            await send_error(ctx, str(e), icon="📅")
            return
        guild_id = guild_id_of(ctx)
        repo = self._reminder_repo(guild_id)
        if await repo.count_for_user(str(ctx.author.id)) >= MAX_REMINDERS_PER_MEMBER:
            await send_error(ctx, self._too_many())
            return
        reminder = Reminder(
            user_id=str(ctx.author.id),
            message=(note or "").strip() or DEFAULT_NOTE,
            remind_time=remind_time,
            jump_url=jump_url(guild_id, channel_id, message_id),
        )
        await repo.add(reminder)
        await ctx.send(
            f"⏰ Je te le rappelle en MP {format_discord_timestamp(remind_time, 'F')} "
            f"({format_discord_timestamp(remind_time, 'R')}).",
            ephemeral=True,
        )

    @staticmethod
    def _too_many() -> str:
        return (
            f"Tu as déjà {MAX_REMINDERS_PER_MEMBER} rappels sur ce serveur : "
            "supprimes-en un avec `/reminder remove`."
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
        repo = self._reminder_repo(gid)

        if await repo.count_for_user(str(ctx.author.id)) >= MAX_REMINDERS_PER_MEMBER:
            await ctx.send(
                f"Tu as déjà {MAX_REMINDERS_PER_MEMBER} rappels sur ce serveur.", ephemeral=True
            )
            return
        new_time = clock.now() + timedelta(minutes=minutes)
        snoozed = Reminder(
            user_id=str(ctx.author.id),
            # Snoozing a snoozed reminder doesn't stack the mark.
            message=snoozed_text(ctx.message.content if ctx.message else None),
            remind_time=new_time,
        )
        try:
            await repo.add(snoozed)
        except Exception as e:
            log_failure(logger, "Failed to snooze reminder %s: %s", reminder_id, e)
            await ctx.send("Impossible de reporter le rappel.", ephemeral=True)
            return

        await ctx.send(
            f"Rappel reporté de {minutes} minutes "
            f"({timestamp_converter(new_time).format(TimestampStyles.RelativeTime)}).",
            ephemeral=True,
        )

    @Task.create(IntervalTrigger(minutes=1))
    async def check_reminders(self):
        now = clock.now()
        for gid in UTILS.guild_ids():
            repo = self._reminder_repo(gid)
            try:
                due = await repo.list_due(now)
            except Exception as e:
                log_failure(logger, "Failed to fetch due reminders for guild %s: %s", gid, e)
                continue

            for reminder in due:
                if reminder.id is None:
                    continue
                next_time = next_after(reminder.remind_time, reminder.frequency, now)
                try:
                    claimed = await repo.claim_due(reminder, next_time)
                except Exception as e:
                    log_failure(
                        logger, "Failed to update reminder %s in guild %s: %s", reminder.id, gid, e
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
                await user.send(reminder_text(reminder), components=components)
                logger.info("Reminder sent to %s: %s", user.display_name, reminder.message)
        except Exception as e:
            logger.warning("Failed to send reminder to user %s: %s", reminder.user_id, e)


def setup(bot: Client) -> None:
    RemindersExtension(bot)


__all__ = ["RemindersExtension", "setup"]
