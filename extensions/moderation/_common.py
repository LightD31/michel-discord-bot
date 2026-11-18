"""Config schema, logger, and shared helpers for the moderation extension."""

import os
from typing import Any

from features.moderation.spam import (
    MAX_SPAM_THRESHOLD,
    MAX_SPAM_WINDOW_SECONDS,
    MIN_SPAM_THRESHOLD,
    MIN_SPAM_WINDOW_SECONDS,
)
from src.core import logging as logutil
from src.core.config import ModuleConfig
from src.discord_ext.look import Kind
from src.discord_ext.staff import is_staff, top_role_position
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(__name__)


@register_module("moduleModeration")
class ModerationConfig(SchemaBase):
    __label__ = "Modération"
    __description__ = "Avertissements, exclusions, bannissements, historique et automod."
    __icon__ = "🛡️"
    __category__ = "Modération"

    enabled: bool = enabled_field()
    modLogChannelId: str = ui(
        "Salon des logs de modération",
        "channel",
        required=True,
        description=(
            "Salon où chaque action de modération est enregistrée, et où arrivent les "
            "signalements des membres (clic droit sur un message → Applications → 🚩 Signaler). "
            "Réserve-le aux modérateurs : les signalements y disent qui a signalé."
        ),
    )
    staffRoleId: str | None = ui(
        "Rôle staff",
        "role",
        description="Rôle exempté de l'automod (les administrateurs le sont toujours).",
        role_use="staff",
    )
    dmOnAction: bool = ui(
        "Prévenir le membre en MP",
        "boolean",
        default=True,
        description="Envoyer un message privé au membre lors d'une sanction.",
    )
    notifyReporters: bool = ui(
        "Remercier qui signale",
        "boolean",
        default=True,
        description=(
            "Envoyer un MP aux membres qui ont signalé un message une fois que les modérateurs "
            "l'ont traité (sans dire comment)."
        ),
    )
    antiInvite: bool = ui(
        "Anti-invitations",
        "boolean",
        default=False,
        description="Supprimer les messages contenant une invitation Discord.",
    )
    antiSpam: bool = ui(
        "Anti-spam",
        "boolean",
        default=False,
        description="Supprimer les messages d'un membre qui poste trop vite.",
    )
    spamThreshold: int = ui(
        "Seuil anti-spam (messages)",
        "number",
        ge=MIN_SPAM_THRESHOLD,
        le=MAX_SPAM_THRESHOLD,
        default=5,
        description="Nombre de messages dans la fenêtre déclenchant l'anti-spam.",
    )
    spamWindowSeconds: int = ui(
        "Fenêtre anti-spam (secondes)",
        "number",
        ge=MIN_SPAM_WINDOW_SECONDS,
        le=MAX_SPAM_WINDOW_SECONDS,
        default=7,
        description="Intervalle de temps évalué pour l'anti-spam.",
    )
    bannedWords: list[str] = ui(
        "Mots interdits",
        "list",
        description="Mots dont la présence supprime le message (insensible à la casse).",
    )
    ignoredChannelIds: list[str] = ui(
        "Salons ignorés (automod)",
        "channellist",
        description="Salons où l'automod ne supprime rien.",
    )
    ignoredRoleIds: list[str] = ui(
        "Rôles ignorés (automod)",
        "rolelist",
        description="Les membres qui ont l'un de ces rôles échappent à l'automod.",
        role_use="staff",
    )


MODERATION = ModuleConfig("moduleModeration")

# Sentinel moderator id used for automated (automod) actions.
AUTOMOD_MODERATOR_ID = "automod"

TYPE_LABELS: dict[str, str] = {
    "warn": "⚠️ Avertissement",
    "timeout": "🔇 Exclusion temporaire",
    "untimeout": "🔊 Fin d'exclusion",
    "kick": "👢 Expulsion",
    "ban": "🔨 Bannissement",
    "unban": "♻️ Débannissement",
    "note": "📝 Note",
    "automod": "🛡️ Automod",
}

# The kind of card each case is posted as (its colour, src/discord_ext/look.py).
TYPE_KINDS: dict[str, Kind] = {
    "warn": "warning",
    "timeout": "warning",
    "untimeout": "success",
    "kick": "error",
    "ban": "error",
    "unban": "success",
    "note": "neutral",
    "automod": "warning",
}


def get_guild_settings(guild_id: int | str) -> dict | None:
    """Return the per-guild module config, or None if disabled/missing."""
    return MODERATION.get(guild_id) or None


def can_moderate(actor: Any, target: Any, me: Any) -> str | None:
    """Return a French error string if *actor* may not moderate *target*, else None.

    *me* is the bot's own Member, used for the bot-hierarchy check. Standard
    Discord role-hierarchy rules apply: the guild owner bypasses the actor check
    but the bot must still outrank the target to act.
    """
    if int(target.id) == int(actor.id):
        return "Tu ne peux pas te modérer toi-même."
    if me is not None and int(target.id) == int(me.id):
        return "Je ne peux pas me modérer moi-même."

    guild = getattr(target, "guild", None)
    owner_id = getattr(guild, "_owner_id", None) or getattr(guild, "owner_id", None)
    if owner_id is not None and int(target.id) == int(owner_id):
        return "Impossible de modérer le propriétaire du serveur."

    actor_is_owner = owner_id is not None and int(actor.id) == int(owner_id)
    if not actor_is_owner and top_role_position(target) >= top_role_position(actor):
        return "Tu ne peux pas modérer un membre de rang égal ou supérieur au tien."

    if me is not None and top_role_position(target) >= top_role_position(me):
        return (
            "Mon rôle est trop bas dans la hiérarchie pour modérer ce membre : un administrateur "
            "peut le remonter au-dessus du sien (Paramètres du serveur → Rôles)."
        )

    return None
