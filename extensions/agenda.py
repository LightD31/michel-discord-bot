"""L'agenda: what's coming up on the server, in ``/agenda`` and a message kept up to date (P19, A5).

The next days of the server in one place: Discord's scheduled events
(including those Twitch and the Zevent manage), birthdays, giveaways and polls
closing, the Secret Santa exchange (:mod:`src.discord_ext.agenda`). Personal
reminders are never listed.

``/agenda`` shows it to the member alone. Where the admins chose a channel,
every 30 minutes the task keeps one message there up to date (the
``agenda`` slot of ``bot_messages``), pinned if they asked: a Components V2
card (P22), the days stacked in the module's colour. The dashboard's
server page has the same days as a calendar.
"""

from __future__ import annotations

from interactions import (
    Buckets,
    Client,
    Extension,
    IntervalTrigger,
    MessageFlags,
    SlashContext,
    Task,
    cooldown,
    listen,
    slash_command,
)

from features.agenda import DEFAULT_DAYS, MAX_DAYS, MIN_DAYS, bounded_days
from src.core import clock, log_context
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.core.logging import log_failure
from src.discord_ext.agenda import agenda_card, agenda_embed, gather_agenda
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.messages import (
    edit_message_if_changed,
    fetch_or_create_persistent_message,
    forget_persistent_message,
    guild_id_of,
)
from src.discord_ext.scopes import guild_scopes
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(__name__)

SLOT = "agenda"


@register_module("moduleAgenda")
class AgendaConfig(SchemaBase):
    __label__ = "Agenda"
    __description__ = (
        "Ce qui arrive sur le serveur : événements, anniversaires, giveaways et sondages qui "
        "se terminent, échange du Père Noël secret."
    )
    __icon__ = "🗓️"
    __category__ = "Communauté"
    __help__ = ModuleHelp(
        "Les prochains jours du serveur en un coup d'œil.",
        ("/agenda",),
        "Les événements Discord, les anniversaires, les giveaways et sondages qui se terminent "
        "et l'échange du Père Noël secret, jour par jour. Tes rappels perso n'y sont jamais.",
    )

    enabled: bool = enabled_field()
    agendaDays: int = ui(
        "Jours affichés",
        "number",
        ge=MIN_DAYS,
        le=MAX_DAYS,
        default=DEFAULT_DAYS,
        description="Combien de jours l'agenda couvre, aujourd'hui compris.",
    )
    agendaChannelId: str = ui(
        "Salon de l'agenda",
        "channel",
        required=False,
        description=(
            "Un message y est tenu à jour toutes les 30 minutes. Vide : seulement /agenda et "
            "le dashboard."
        ),
    )
    agendaPin: bool = ui(
        "Épingler le message",
        "boolean",
        default=True,
        description="Demande la permission de gérer les messages dans ce salon.",
    )


AGENDA = ModuleConfig("moduleAgenda")


class AgendaExtension(Extension):
    # The agenda reads these modules' dates, only where they are on.
    MODULE_KEYS = (
        "moduleAgenda",
        "moduleBirthday",
        "moduleGiveaway",
        "moduleUtils",
        "moduleSecretSanta",
    )
    HELP_LABEL = "🗓️ Agenda"

    def __init__(self, bot: Client):
        self.bot = bot

    @listen()
    async def on_startup(self) -> None:
        if not self.refresh_all.running:
            self.refresh_all.start()

    @Task.create(IntervalTrigger(minutes=30))
    async def refresh_all(self) -> None:
        for guild_id, settings in AGENDA.items():
            if not settings.get("agendaChannelId"):
                continue
            with log_context.scope(guild_id=guild_id):
                try:
                    await self.refresh(guild_id, settings)
                except Exception as e:
                    log_failure(logger, "Agenda of guild %s not refreshed: %s", guild_id, e)

    async def refresh(self, guild_id: str, settings: dict) -> bool:
        """Bring the guild's agenda message up to date; ``True`` when it changed."""
        message = await fetch_or_create_persistent_message(
            self.bot,
            guild_id=guild_id,
            slot=SLOT,
            channel_id=settings.get("agendaChannelId"),
            label="Agenda",
            initial_content="🗓️ L'agenda arrive…",
            pin=bool(settings.get("agendaPin", True)),
            logger=logger,
            v2=True,
        )
        if message is None:
            return False
        agenda = await gather_agenda(
            self.bot, guild_id, clock.now(), bounded_days(settings.get("agendaDays"))
        )
        try:
            return await edit_message_if_changed(
                message,
                components=[agenda_card(agenda)],
                flags=MessageFlags.IS_COMPONENTS_V2,
                logger=logger,
            )
        except Exception:
            # Deleted by hand, say: the next run posts it again.
            forget_persistent_message(guild_id, SLOT)
            raise

    @slash_command(
        name="agenda",
        description="Ce qui arrive sur le serveur ces prochains jours",
        scopes=guild_scopes(AGENDA.guild_ids()),
    )
    @cooldown(Buckets.USER, 3, 30)
    async def agenda(self, ctx: SlashContext) -> None:
        guild_id = guild_id_of(ctx)
        await ctx.defer(ephemeral=True)
        days = bounded_days(AGENDA.get(guild_id).get("agendaDays"))
        agenda = await gather_agenda(self.bot, guild_id, clock.now(), days)
        await ctx.send(embeds=[agenda_embed(agenda)], ephemeral=True, allowed_mentions=NO_PINGS)


def setup(bot: Client) -> None:
    AgendaExtension(bot)


__all__ = ["AGENDA", "SLOT", "AgendaConfig", "AgendaExtension", "setup"]
