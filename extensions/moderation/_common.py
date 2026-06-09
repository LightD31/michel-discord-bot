"""Config schema, logger, and shared helpers for the moderation extension."""

import os
from typing import Any

from interactions import Permissions

from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.embeds import Colors
from src.webui.schemas import SchemaBase, enabled_field, register_module, ui

logger = logutil.init_logger(os.path.basename(__file__))


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
        description="Salon où chaque action de modération est enregistrée.",
    )
    staffRoleId: str | None = ui(
        "Rôle staff",
        "role",
        description="Rôle exempté de l'automod (les administrateurs le sont toujours).",
    )
    dmOnAction: bool = ui(
        "Prévenir le membre en MP",
        "boolean",
        default=True,
        description="Envoyer un message privé au membre lors d'une sanction.",
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
        default=5,
        description="Nombre de messages dans la fenêtre déclenchant l'anti-spam.",
    )
    spamWindowSeconds: int = ui(
        "Fenêtre anti-spam (secondes)",
        "number",
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
        "list",
        description="IDs des salons exemptés de l'automod.",
    )
    ignoredRoleIds: list[str] = ui(
        "Rôles ignorés (automod)",
        "list",
        description="IDs des rôles exemptés de l'automod.",
    )


_, module_config, enabled_servers = load_config("moduleModeration")
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore[misc]

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

TYPE_COLORS: dict[str, int] = {
    "warn": Colors.WARNING,
    "timeout": Colors.WARNING,
    "untimeout": Colors.SUCCESS,
    "kick": Colors.ERROR,
    "ban": Colors.ERROR,
    "unban": Colors.SUCCESS,
    "note": Colors.INFO,
    "automod": Colors.ORANGE,
}


def get_guild_settings(guild_id: int | str) -> dict | None:
    """Return the per-guild module config, or None if disabled/missing."""
    sid = str(guild_id)
    settings = module_config.get(sid)
    if settings is None and sid.isdigit():
        settings = module_config.get(int(sid))
    return settings


def _top_role_position(member: Any) -> int:
    """Highest role position held by *member* (0 if none / on error)."""
    try:
        positions = [getattr(r, "position", 0) or 0 for r in getattr(member, "roles", []) or []]
        return max(positions) if positions else 0
    except Exception:
        return 0


def is_staff(member: Any, settings: dict) -> bool:
    """True if *member* is an administrator or holds the configured staff role."""
    try:
        if member.has_permission(Permissions.ADMINISTRATOR):
            return True
    except Exception:
        pass
    staff_role_id = settings.get("staffRoleId")
    if not staff_role_id:
        return False
    try:
        return any(int(getattr(r, "id", r)) == int(staff_role_id) for r in member.roles)
    except Exception:
        return False


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
    if not actor_is_owner and _top_role_position(target) >= _top_role_position(actor):
        return "Tu ne peux pas modérer un membre de rang égal ou supérieur au tien."

    if me is not None and _top_role_position(target) >= _top_role_position(me):
        return "Mon rôle est trop bas dans la hiérarchie pour modérer ce membre."

    return None
