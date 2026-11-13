"""Which roles may go in a self-service role menu, and who may put them there.

A role menu lets *every* member toggle the listed roles with one click, and the
bot hands those roles out with its own permissions. Without these rules,
building a menu is a privilege escalation: the dashboard only asks for
``MANAGE_GUILD``, which does not let anyone assign roles by hand.

1. A role carrying a moderation or administration permission is never
   self-assignable, whoever builds the menu: one click would make any member
   staff. The button handler re-checks this at click time, since a role can
   gain permissions after the menu was posted.
2. Whoever builds the menu must be able to hand the role out by hand, as
   Discord itself requires: ``MANAGE_ROLES`` (or ``ADMINISTRATOR``) and a
   highest role strictly above it. The guild owner and the bot's developers
   skip this rule. It matters even for roles with no permission at all, which
   can still unlock private channels through channel overwrites.
3. The role must sit below the bot's highest role, or Discord rejects the
   toggle at click time.

The same rules hold wherever the bot hands out a role a guild's admins chose
(an XP level reward, the birthday role: :func:`grant_refusal`), where a role
opens a service outside Discord (LLDAP groups: :func:`access_refusal`, rule 2
alone), and a role the bot pings must be one the admin could ping
(:func:`mention_refusal`). The dashboard applies them to the roles a module
save adds (``src/discord_ext/role_grants.py``); the bot re-applies rule 1 each
time it grants a role.

Pure logic: callers translate ``interactions`` objects into
:class:`RoleInfo` / :class:`MenuAuthor`.
"""

from dataclasses import dataclass

from src.core.permissions import (
    ADMINISTRATOR,
    MENTION_EVERYONE,
    can_manage_roles,
    has_any,
    is_privileged,
)


@dataclass(frozen=True)
class RoleInfo:
    """The parts of a Discord role these rules look at."""

    id: str
    position: int
    permissions: int
    managed: bool = False
    is_default: bool = False  # @everyone
    mentionable: bool = False


@dataclass(frozen=True)
class MenuAuthor:
    """Whoever is building or editing the menu (or saving a setting naming a role)."""

    permissions: int
    top_position: int
    is_owner: bool = False
    # Bot developers (``webui.developerUserIds``) operate the bot itself and
    # may not even be members of the guild: only rules 1 and 3 apply.
    bypass_hierarchy: bool = False


def _hierarchy_refusal(role: RoleInfo, author: MenuAuthor, purpose: str) -> str | None:
    """Rule 2: *author* could hand *role* out by hand (``MANAGE_ROLES``, higher role)."""
    if author.is_owner or author.bypass_hierarchy:
        return None
    if not can_manage_roles(author.permissions):
        return f"Il faut la permission « Gérer les rôles » pour {purpose}."
    if role.position >= author.top_position:
        return f"Le rôle <@&{role.id}> n'est pas sous votre rôle le plus haut."
    return None


def grant_refusal(
    role: RoleInfo, *, author: MenuAuthor, bot_top_position: int, purpose: str
) -> str | None:
    """Why the bot may not hand *role* out on *author*'s behalf, or ``None`` if it may.

    The three rules above. *purpose* completes "Il faut la permission « Gérer
    les rôles » pour …" (``"créer un menu de rôles"``).
    """
    mention = f"<@&{role.id}>"
    if role.is_default:
        return "@everyone ne peut pas être attribué par le bot."
    if role.managed:
        return f"Le rôle {mention} est géré par une intégration et ne peut pas être attribué."
    if is_privileged(role.permissions):
        return (
            f"Le rôle {mention} donne des permissions de modération ou d'administration : "
            "le bot ne l'attribue pas automatiquement."
        )
    if role.position >= bot_top_position:
        return f"Le rôle {mention} est au-dessus du rôle le plus haut du bot."
    return _hierarchy_refusal(role, author, purpose)


def role_menu_refusal(role: RoleInfo, *, author: MenuAuthor, bot_top_position: int) -> str | None:
    """Why *role* can't go in a menu built by *author*, or ``None`` if it can."""
    return grant_refusal(
        role, author=author, bot_top_position=bot_top_position, purpose="créer un menu de rôles"
    )


def access_refusal(role: RoleInfo, *, author: MenuAuthor, purpose: str) -> str | None:
    """Rule 2 alone, for a role that unlocks something outside Discord (an LLDAP group).

    The bot gives the role nothing, so a staff role may be named; but whoever
    names it must be able to hand it out themselves, or they could open the
    outside service to a role they can't control. @everyone is refused too.
    """
    if role.is_default:
        return "@everyone ne peut pas être utilisé ici."
    return _hierarchy_refusal(role, author, purpose)


def mention_refusal(role: RoleInfo, *, author: MenuAuthor) -> str | None:
    """Why the bot may not ping *role* for *author*, or ``None`` if it may.

    The bot (an administrator) can ping any role; a member only a mentionable
    one, unless they hold ``MENTION_EVERYONE``.
    """
    if role.is_default:
        return "Utilisez @everyone dans le texte plutôt qu'en rôle à mentionner."
    if role.mentionable or author.is_owner or author.bypass_hierarchy:
        return None
    if has_any(author.permissions, ADMINISTRATOR | MENTION_EVERYONE):
        return None
    return (
        f"Le rôle <@&{role.id}> n'est pas mentionnable : il faut la permission "
        "« Mentionner @everyone, @here et tous les rôles » pour le choisir."
    )


def self_assign_refusal(role: RoleInfo) -> str | None:
    """Why a member can't toggle *role* themselves right now, or ``None``."""
    if is_privileged(role.permissions):
        return "Ce rôle donne des permissions de modération : il ne peut pas être auto-attribué."
    return None


__all__ = [
    "MenuAuthor",
    "RoleInfo",
    "access_refusal",
    "grant_refusal",
    "mention_refusal",
    "role_menu_refusal",
    "self_assign_refusal",
]
