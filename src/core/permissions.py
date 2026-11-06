"""Discord permission bits as plain integers.

Kept framework-free so ``features/`` and the Web UI can reason about guild
permissions without importing ``interactions``, whose ``Permissions`` flag
enum carries the same values (``tests/test_core_permissions.py`` pins them).
"""

KICK_MEMBERS = 1 << 1
BAN_MEMBERS = 1 << 2
ADMINISTRATOR = 1 << 3
MANAGE_CHANNELS = 1 << 4
MANAGE_GUILD = 1 << 5
VIEW_AUDIT_LOG = 1 << 7
MANAGE_MESSAGES = 1 << 13
MENTION_EVERYONE = 1 << 17
MUTE_MEMBERS = 1 << 22
DEAFEN_MEMBERS = 1 << 23
MOVE_MEMBERS = 1 << 24
MANAGE_NICKNAMES = 1 << 27
MANAGE_ROLES = 1 << 28
MANAGE_WEBHOOKS = 1 << 29
MANAGE_GUILD_EXPRESSIONS = 1 << 30
MANAGE_EVENTS = 1 << 33
MANAGE_THREADS = 1 << 34
MODERATE_MEMBERS = 1 << 40

# Moderation and administration powers: a role carrying any of them makes its
# holder staff. Used to keep such roles out of anything self-service.
PRIVILEGED = (
    KICK_MEMBERS
    | BAN_MEMBERS
    | ADMINISTRATOR
    | MANAGE_CHANNELS
    | MANAGE_GUILD
    | VIEW_AUDIT_LOG
    | MANAGE_MESSAGES
    | MENTION_EVERYONE
    | MUTE_MEMBERS
    | DEAFEN_MEMBERS
    | MOVE_MEMBERS
    | MANAGE_NICKNAMES
    | MANAGE_ROLES
    | MANAGE_WEBHOOKS
    | MANAGE_GUILD_EXPRESSIONS
    | MANAGE_EVENTS
    | MANAGE_THREADS
    | MODERATE_MEMBERS
)


def has_any(permissions: int, flags: int) -> bool:
    """True if *permissions* holds at least one of the bits in *flags*."""
    return bool(permissions & flags)


def is_privileged(permissions: int) -> bool:
    """True if *permissions* grants any moderation or administration power."""
    return has_any(permissions, PRIVILEGED)


def can_manage_guild(permissions: int, *, is_owner: bool = False) -> bool:
    """Who the dashboard treats as a guild's admin: owner, ADMINISTRATOR or MANAGE_GUILD."""
    return is_owner or has_any(permissions, ADMINISTRATOR | MANAGE_GUILD)


def can_manage_roles(permissions: int, *, is_owner: bool = False) -> bool:
    """Owner, ADMINISTRATOR or MANAGE_ROLES — what Discord requires to hand out a role."""
    return is_owner or has_any(permissions, ADMINISTRATOR | MANAGE_ROLES)


__all__ = [
    "ADMINISTRATOR",
    "BAN_MEMBERS",
    "DEAFEN_MEMBERS",
    "KICK_MEMBERS",
    "MANAGE_CHANNELS",
    "MANAGE_EVENTS",
    "MANAGE_GUILD",
    "MANAGE_GUILD_EXPRESSIONS",
    "MANAGE_MESSAGES",
    "MANAGE_NICKNAMES",
    "MANAGE_ROLES",
    "MANAGE_THREADS",
    "MANAGE_WEBHOOKS",
    "MENTION_EVERYONE",
    "MODERATE_MEMBERS",
    "MOVE_MEMBERS",
    "MUTE_MEMBERS",
    "PRIVILEGED",
    "VIEW_AUDIT_LOG",
    "can_manage_guild",
    "can_manage_roles",
    "has_any",
    "is_privileged",
]
