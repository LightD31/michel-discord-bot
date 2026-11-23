"""Reminders Discord extension — /rappel slash command and due-check task.

Slash commands:
- ``/rappel creer`` — create a reminder for yourself: ``quand:`` reads "samedi 9h",
  "dans 2h" (:mod:`features.reminders.when`), its autocomplete echoing the
  reading; optional recurrence
- ``/rappel liste`` — your reminders on this server and when they next fire
- ``/rappel supprimer`` — a menu of your reminders, to delete one or several

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
from datetime import timedelta
from typing import Any

from interactions import (
    ActionRow,
    AutocompleteContext,
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
    StringSelectMenu,
    StringSelectOption,
    Task,
    TimestampStyles,
    component_callback,
    listen,
    message_context_menu,
    modal_callback,
    slash_command,
    slash_option,
)
from interactions.client.utils import timestamp_converter

from features.reminders import (
    MAX_REMINDER_LENGTH,
    MAX_REMINDERS_PER_MEMBER,
    Reminder,
    ReminderRepository,
    next_after,
    schedule_label,
    snooze_minutes,
    snoozed_text,
)
from features.reminders.when import EXAMPLES, parse_when, reading
from src.core import clock, log_context
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.logging import log_failure
from src.core.text import EMBED_DESCRIPTION_LIMIT, clip, format_discord_timestamp
from src.discord_ext.autocomplete import CHOICE_NAME_LIMIT
from src.discord_ext.look import card, ok
from src.discord_ext.menus import menu_help
from src.discord_ext.messages import fetch_user_safe, guild_id_of, jump_url, send_error
from src.discord_ext.scopes import guild_scopes

SNOOZE_PREFIX = "reminder_snooze"
DELETE_MENU = "reminder_delete"
# How much of each reminder's text /rappel liste shows.
LIST_PREVIEW_LENGTH = 120
_SNOOZE_RE = re.compile(rf"^{SNOOZE_PREFIX}:(\d+):([0-9a-fA-F]+)$")
REMIND_MENU = "⏰ Me le rappeler"
REMIND_FORM = "remind_msg"  # remind_msg:{channel_id}:{message_id}
_REMIND_FORM_RE = re.compile(rf"^{REMIND_FORM}:(\d+):(\d+)$")
MAX_NOTE_LENGTH = 500
NO_REMINDER = (
    "Tu n'as aucun rappel sur ce serveur. Crée-en un avec `/rappel creer`, ou d'un clic "
    "droit sur un message → Applications → ⏰ Me le rappeler."
)
DEFAULT_NOTE = "Revoir ce message"

logger = logutil.init_logger(__name__)
UTILS = ModuleConfig("moduleUtils")


def _snooze_minutes(guild_id: str | int) -> int:
    return snooze_minutes(UTILS.get(guild_id))


def when_choices(typed: str) -> list[dict[str, int | float | str]]:
    """The autocomplete of « quand » : what Michel reads in *typed*, or how to write it.

    The value is what the member typed: the command parses it again, at the
    moment it runs.
    """
    now = clock.now()
    typed = typed.strip()[:CHOICE_NAME_LIMIT]
    if not typed:
        return [
            {
                "name": clip(f"{example} → {reading(parse_when(example, now), now)}", 100),
                "value": example,
            }
            for example in EXAMPLES.split(", ")
        ]
    try:
        found = parse_when(typed, now)
    except ValueError as e:
        return [{"name": clip(f"❓ {e}", CHOICE_NAME_LIMIT), "value": typed}]
    return [{"name": clip(f"→ {reading(found, now)}", CHOICE_NAME_LIMIT), "value": typed}]


def delete_menu(reminders: list[Reminder]) -> StringSelectMenu:
    """The menu of ``/rappel supprimer``: one option per reminder, several at once."""
    options = [
        StringSelectOption(
            label=clip(reminder.message, 100),
            value=reminder.id,
            description=clip(schedule_label(reminder), 100),
        )
        for reminder in reminders[:MAX_REMINDERS_PER_MEMBER]
        if reminder.id
    ]
    return StringSelectMenu(
        *options,
        placeholder="Rappels à supprimer",
        min_values=1,
        max_values=len(options),
        custom_id=DELETE_MENU,
    )


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
    return card(
        "moduleUtils",
        title=f"Tes rappels ({len(reminders)}/{MAX_REMINDERS_PER_MEMBER})",
        description=clip("\n\n".join(lines), EMBED_DESCRIPTION_LIMIT),
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
        name="rappel",
        sub_cmd_name="creer",
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
        name="quand",
        description="Ex. dans 2h, demain 18h30, samedi 9h, 24/12 20h",
        opt_type=OptionType.STRING,
        required=True,
        max_length=60,
        autocomplete=True,
        argument_name="when",
    )
    @slash_option(
        "frequence",
        "Le répéter (défaut : une seule fois)",
        OptionType.STRING,
        choices=[
            SlashCommandChoice(name="Tous les jours", value="daily"),
            SlashCommandChoice(name="Toutes les semaines", value="weekly"),
            SlashCommandChoice(name="Tous les mois", value="monthly"),
            SlashCommandChoice(name="Tous les ans", value="yearly"),
        ],
        required=False,
        argument_name="frequency",
    )
    async def reminder_set(
        self,
        ctx: SlashContext,
        task: str,
        when: str,
        frequency: str | None = None,
    ):
        try:
            remind_time = parse_when(when, clock.now())
        except ValueError as e:
            await send_error(ctx, str(e), icon="📅")
            return

        repo = self._reminder_repo(guild_id_of(ctx))
        if await repo.count_for_user(str(ctx.author.id)) >= MAX_REMINDERS_PER_MEMBER:
            await send_error(ctx, self._too_many())
            return

        reminder = Reminder(
            user_id=str(ctx.author.id),
            message=task,
            remind_time=remind_time,
            frequency=frequency,  # type: ignore[arg-type]
        )
        await repo.add(reminder)

        await ctx.send(
            f"⏰ Rappel créé : **{schedule_label(reminder)}** "
            f"({format_discord_timestamp(remind_time, 'R')})\n{task}",
            ephemeral=True,
        )
        logger.info(
            "Reminder set for %s at %s (%s)",
            ctx.author.display_name,
            clock.to_local(remind_time).strftime("%H:%M"),
            frequency,
        )

    @reminder_set.autocomplete("quand")
    async def when_choices(self, ctx: AutocompleteContext) -> None:
        """Read « quand » back as it is typed: "samedi 9h" → "samedi 21 novembre à 09:00"."""
        await ctx.send(choices=when_choices(ctx.input_text))

    @reminder_set.subcommand(
        sub_cmd_name="liste",
        sub_cmd_description="Affiche tes rappels sur ce serveur",
    )
    async def list_reminders(self, ctx: SlashContext):
        reminders = await self._reminder_repo(guild_id_of(ctx)).list_for_user(str(ctx.author.id))
        if not reminders:
            await ctx.send(f"⏰ {NO_REMINDER}", ephemeral=True)
            return
        await ctx.send(embeds=[reminders_embed(reminders)], ephemeral=True)

    @reminder_set.subcommand(
        sub_cmd_name="supprimer",
        sub_cmd_description="Supprime un ou plusieurs de tes rappels",
    )
    async def delete_reminder(self, ctx: SlashContext):
        reminders = await self._reminder_repo(guild_id_of(ctx)).list_for_user(str(ctx.author.id))
        if not reminders:
            await send_error(ctx, NO_REMINDER, icon="⏰")
            return
        await ctx.send(
            "Choisis les rappels à supprimer :",
            components=[ActionRow(delete_menu(reminders))],
            ephemeral=True,
        )

    @component_callback(DELETE_MENU)
    async def on_delete_chosen(self, ctx: ComponentContext) -> None:
        deleted = await self._reminder_repo(guild_id_of(ctx)).delete_mine(
            str(ctx.author.id), list(ctx.values)
        )
        if not deleted:
            await ctx.edit_origin(content="⏰ Ces rappels n'existent plus.", components=[])
            return
        word = "rappel supprimé" if deleted == 1 else "rappels supprimés"
        await ctx.edit_origin(content=ok(f"{deleted} {word}."), components=[])
        logger.info("%d reminder(s) deleted for %s", deleted, ctx.author.display_name)

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
            "supprimes-en un avec `/rappel supprimer`."
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
            with log_context.scope(guild_id=gid):
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
                            logger,
                            "Failed to update reminder %s in guild %s: %s",
                            reminder.id,
                            gid,
                            e,
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
