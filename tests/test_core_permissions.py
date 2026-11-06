"""The framework-free permission bits must match Discord's (and interactions.py's)."""

import pytest
from interactions import Permissions

from src.core import permissions as perms

PINNED = [
    "KICK_MEMBERS",
    "BAN_MEMBERS",
    "ADMINISTRATOR",
    "MANAGE_CHANNELS",
    "MANAGE_GUILD",
    "VIEW_AUDIT_LOG",
    "MANAGE_MESSAGES",
    "MENTION_EVERYONE",
    "MUTE_MEMBERS",
    "DEAFEN_MEMBERS",
    "MOVE_MEMBERS",
    "MANAGE_NICKNAMES",
    "MANAGE_ROLES",
    "MANAGE_WEBHOOKS",
    "MANAGE_GUILD_EXPRESSIONS",
    "MANAGE_EVENTS",
    "MANAGE_THREADS",
    "MODERATE_MEMBERS",
]


@pytest.mark.parametrize("name", PINNED)
def test_bits_match_interactions(name):
    assert getattr(perms, name) == int(getattr(Permissions, name))


def test_privileged_covers_every_pinned_power():
    for name in PINNED:
        assert perms.is_privileged(getattr(perms, name)), name


def test_harmless_permissions_are_not_privileged():
    harmless = (
        Permissions.VIEW_CHANNEL
        | Permissions.SEND_MESSAGES
        | Permissions.ADD_REACTIONS
        | Permissions.CONNECT
        | Permissions.SPEAK
        | Permissions.CHANGE_NICKNAME
        | Permissions.USE_APPLICATION_COMMANDS
    )
    assert not perms.is_privileged(int(harmless))


def test_guild_and_role_management_helpers():
    assert perms.can_manage_guild(perms.MANAGE_GUILD)
    assert perms.can_manage_guild(perms.ADMINISTRATOR)
    assert perms.can_manage_guild(0, is_owner=True)
    assert not perms.can_manage_guild(perms.MANAGE_ROLES)

    assert perms.can_manage_roles(perms.MANAGE_ROLES)
    assert perms.can_manage_roles(perms.ADMINISTRATOR)
    assert perms.can_manage_roles(0, is_owner=True)
    assert not perms.can_manage_roles(perms.MANAGE_GUILD)
