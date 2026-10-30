"""LLDAP accounts and role → group sync (pure logic; the client is ``src.integrations.lldap``)."""

from features.lldap.accounts import (
    USERNAME_MAX_LENGTH,
    generate_temp_password,
    normalize_email,
    normalize_username,
    username_from_discord,
)
from features.lldap.sync import (
    GroupChange,
    GuildRoster,
    SyncReport,
    describe_report,
    merge_rosters,
    parse_role_groups,
    plan_sync,
)

__all__ = [
    "USERNAME_MAX_LENGTH",
    "GroupChange",
    "GuildRoster",
    "SyncReport",
    "describe_report",
    "generate_temp_password",
    "merge_rosters",
    "normalize_email",
    "normalize_username",
    "parse_role_groups",
    "plan_sync",
    "username_from_discord",
]
