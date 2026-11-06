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

Pure logic: callers translate ``interactions`` objects into
:class:`RoleInfo` / :class:`MenuAuthor`.
"""

from dataclasses import dataclass

from src.core.permissions import can_manage_roles, is_privileged


@dataclass(frozen=True)
class RoleInfo:
    """The parts of a Discord role these rules look at."""

    id: str
    position: int
    permissions: int
    managed: bool = False
    is_default: bool = False  # @everyone


@dataclass(frozen=True)
class MenuAuthor:
    """Whoever is building or editing the menu."""

    permissions: int
    top_position: int
    is_owner: bool = False
    # Bot developers (``webui.developerUserIds``) operate the bot itself and
    # may not even be members of the guild: only rules 1 and 3 apply.
    bypass_hierarchy: bool = False


def role_menu_refusal(role: RoleInfo, *, author: MenuAuthor, bot_top_position: int) -> str | None:
    """Why *role* can't go in a menu built by *author*, or ``None`` if it can."""
    mention = f"<@&{role.id}>"
    if role.is_default:
        return "@everyone ne peut pas figurer dans un menu de rôles."
    if role.managed:
        return f"Le rôle {mention} est géré par une intégration et ne peut pas être attribué."
    if is_privileged(role.permissions):
        return (
            f"Le rôle {mention} donne des permissions de modération ou d'administration : "
            "il ne peut pas être auto-attribué."
        )
    if role.position >= bot_top_position:
        return f"Le rôle {mention} est au-dessus du rôle le plus haut du bot."
    if author.is_owner or author.bypass_hierarchy:
        return None
    if not can_manage_roles(author.permissions):
        return "Il faut la permission « Gérer les rôles » pour créer un menu de rôles."
    if role.position >= author.top_position:
        return f"Le rôle {mention} n'est pas sous votre rôle le plus haut."
    return None


def self_assign_refusal(role: RoleInfo) -> str | None:
    """Why a member can't toggle *role* themselves right now, or ``None``."""
    if is_privileged(role.permissions):
        return "Ce rôle donne des permissions de modération : il ne peut pas être auto-attribué."
    return None


__all__ = ["MenuAuthor", "RoleInfo", "role_menu_refusal", "self_assign_refusal"]
