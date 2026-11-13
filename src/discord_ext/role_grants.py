"""Roles and mass mentions a guild's admins set up, held to the Discord rules.

The dashboard lets ``MANAGE_GUILD`` in, but the bot acts on its settings with
its own permissions (an administrator on most servers): it hands out the XP
rewards and the birthday role, pings the roles it is told to, links roles to
LLDAP groups and posts templates as written. Without these checks, a member
who may only manage the server's settings could make the bot give them an
administrator role, or ping every member, which they could not do by hand.

Two moments:

* **Save.** :func:`settings_refusal` judges what a module save *adds* (roles,
  ``@everyone`` / ``@here`` in a text), with the rules of
  ``features/reactionroles/permissions.py`` (bot loop only).
* **Grant.** :func:`auto_grant_refusal` is re-checked each time the bot hands
  out a configured role, since a role can gain permissions after the save.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from features.reactionroles.permissions import (
    MenuAuthor,
    RoleInfo,
    access_refusal,
    grant_refusal,
    mention_refusal,
    self_assign_refusal,
)
from src.core.permissions import ADMINISTRATOR, MENTION_EVERYONE, has_any
from src.discord_ext.rolemenus import bot_top_position, menu_author, role_info

MASS_MENTION_REFUSAL = (
    "Il faut la permission « Mentionner @everyone, @here et tous les rôles » "
    "pour écrire @everyone ou @here dans un message du bot."
)


def can_mention_everyone(author: MenuAuthor) -> bool:
    """Whether *author* could write @everyone / @here in Discord themselves."""
    return (
        author.is_owner
        or author.bypass_hierarchy
        or has_any(author.permissions, ADMINISTRATOR | MENTION_EVERYONE)
    )


def role_refusal(use: str, role: RoleInfo, *, author: MenuAuthor, bot_position: int) -> str | None:
    """Why *author* may not pick *role* for *use* (``schemas.ROLE_USES``), or ``None``."""
    if use == "grant":
        return grant_refusal(
            role,
            author=author,
            bot_top_position=bot_position,
            purpose="faire attribuer un rôle par le bot",
        )
    if use == "mention":
        return mention_refusal(role, author=author)
    if use == "access":
        return access_refusal(role, author=author, purpose="relier un rôle à un groupe LLDAP")
    return None  # "staff": the dashboard bar is the rule


async def settings_refusal(
    bot: Any,
    guild_id: str,
    user_id: str,
    *,
    roles: Iterable[tuple[str, str]],
    mass_mentions: bool,
    is_developer: bool,
) -> str | None:
    """Why *user_id* may not save these additions to *guild_id*'s settings, or ``None``.

    *roles* holds the ``(use, role id)`` pairs the save adds, *mass_mentions*
    whether it adds a text with @everyone or @here. Bot loop only.
    """
    guild = bot.get_guild(int(guild_id))
    if guild is None:
        return "Serveur introuvable."
    member = await guild.fetch_member(int(user_id))
    author = menu_author(guild, member, bypass_hierarchy=is_developer)
    if mass_mentions and not can_mention_everyone(author):
        return MASS_MENTION_REFUSAL
    checked = sorted(pair for pair in roles if pair[0] != "staff")
    if not checked:
        return None
    bot_position = await bot_top_position(bot, guild)
    for use, role_id in checked:
        role = guild.get_role(int(role_id)) if role_id.isdigit() else None
        if role is None:
            return f"Rôle introuvable sur ce serveur : {role_id}"
        refusal = role_refusal(
            use, role_info(role, guild.id), author=author, bot_position=bot_position
        )
        if refusal:
            return refusal
    return None


def auto_grant_refusal(role: RoleInfo) -> str | None:
    """Why the bot must not hand *role* out on its own right now, or ``None``."""
    if role.is_default:
        return "@everyone ne s'attribue pas."
    if role.managed:
        return "rôle géré par une intégration"
    return self_assign_refusal(role)


def grantable_role(guild: Any, role_id: Any) -> tuple[Any | None, str | None]:
    """The role of *guild* the bot may grant as *role_id*, else ``(None, reason)``."""
    try:
        role = guild.get_role(int(role_id))
    except (TypeError, ValueError):
        role = None
    if role is None:
        return None, "rôle introuvable"
    refusal = auto_grant_refusal(role_info(role, guild.id))
    if refusal:
        return None, refusal
    return role, None


__all__ = [
    "MASS_MENTION_REFUSAL",
    "auto_grant_refusal",
    "can_mention_everyone",
    "grantable_role",
    "role_refusal",
    "settings_refusal",
]
