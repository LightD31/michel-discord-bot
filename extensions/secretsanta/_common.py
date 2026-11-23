"""Shared helpers and config for the Secret Santa extension mixins."""

from typing import Any

from interactions import ComponentContext, ModalContext, Permissions, SlashContext

from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.secretsanta import context_guild_id, join_buttons
from src.webui.schemas import ModuleHelp, SchemaBase, enabled_field, register_module, ui

MIN_REMINDER_DAYS, MAX_REMINDER_DAYS = 1, 14
DEFAULT_REMINDER_DAYS = 3


@register_module("moduleSecretSanta")
class SecretSantaConfig(SchemaBase):
    __label__ = "Secret Santa"
    __description__ = (
        "Secret Santa dans Discord : inscriptions avec liste d'envies, exclusions, tirage "
        "et envoi des attributions par MP, messages anonymes entre Père Noël et "
        "destinataire, et suivi des colis pour les échanges par la poste."
    )
    __icon__ = "🎁"
    __category__ = "Événements"
    __help__ = ModuleHelp(
        "Le Père Noël secret : inscriptions avec liste d'envies, tirage, messages anonymes.",
        ("/secretsanta creer", "/secretsanta tirage", "/secretsanta participants"),
        "Après le tirage, chacun reçoit en MP le nom de la personne à gâter, et peut lui écrire anonymement.",
    )

    enabled: bool = enabled_field()
    anonymousMessages: bool = ui(
        "Messages anonymes",
        "boolean",
        default=True,
        description=(
            "Chaque Père Noël peut écrire anonymement à son destinataire, qui peut lui "
            "répondre. Le bot ne garde pas les messages ; un message signalé est transmis "
            "au créateur de la session."
        ),
    )
    reminderDaysBefore: int = ui(
        "Rappel avant l'échange (jours)",
        "number",
        ge=MIN_REMINDER_DAYS,
        le=MAX_REMINDER_DAYS,
        default=DEFAULT_REMINDER_DAYS,
        description=(
            "Sessions avec colis seulement : combien de jours avant la date de l'échange "
            "les Pères Noël qui n'ont pas marqué leur cadeau envoyé reçoivent un rappel."
        ),
    )


logger = logutil.init_logger(__name__)
SANTA = ModuleConfig("moduleSecretSanta")


def relay_enabled(context_id: str) -> bool:
    """Whether the session's members may write to each other through the bot.

    Off where the guild switched the module (or the option) off; a group DM's
    session has no settings and keeps it on.
    """
    guild_id = context_guild_id(context_id)
    if guild_id is None:
        return True
    return SANTA.enabled(guild_id) and bool(SANTA.get(guild_id).get("anonymousMessages", True))


def reminder_days(settings: dict[str, Any]) -> int:
    """The reminder's lead time, clamped like the schema (``config.json`` is hand-editable)."""
    try:
        days = int(settings.get("reminderDaysBefore", DEFAULT_REMINDER_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_REMINDER_DAYS
    return min(max(days, MIN_REMINDER_DAYS), MAX_REMINDER_DAYS)


def get_context_id(ctx: SlashContext | ComponentContext | ModalContext) -> str:
    """Unique id for the session's scope (guild or DM channel)."""
    if ctx.guild:
        return f"guild_{ctx.guild.id}"
    return f"channel_{ctx.channel.id}"


def is_guild_admin(ctx: SlashContext | ComponentContext | ModalContext) -> bool:
    """Whether the author is an administrator of the server the command runs in."""
    has_permission = getattr(ctx.author, "has_permission", None)
    return (
        bool(ctx.guild)
        and callable(has_permission)
        and bool(has_permission(Permissions.ADMINISTRATOR))
    )


def may_organise(ctx: SlashContext | ComponentContext, session) -> bool:
    """Whether the author runs *session*: its creator, or an administrator.

    With no session, only an administrator: exclusions outlive a session, and
    nobody but the organiser may decide who can't draw whom.
    """
    if session is not None and ctx.author.id == session.created_by:
        return True
    return is_guild_admin(ctx)


ORGANISER_ONLY = "Seul le créateur de la session ou un administrateur peut gérer les exclusions."

# Kept under its old name for the mixins.
create_join_buttons = join_buttons
