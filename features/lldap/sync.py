"""Role → LLDAP group sync planning.

Each guild maps Discord roles to LLDAP group ids. The members holding a role
should be in its group; linked LLDAP users (those carrying a Discord id) who
no longer hold any role mapped to a group leave it. Users without a Discord id
(admins, accounts made by hand) are never touched.

Guilds pointing at the same LLDAP server share its groups, so their role
holders are merged per server before planning: two guilds never undo each
other's changes. A group that some guild could not read (guild unavailable,
role deleted) is *blocked* and left alone for the whole run, since an
incomplete view would empty it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field
from typing import Any

from src.integrations.lldap import LldapUser


@dataclass(frozen=True)
class GroupChange:
    group_id: int
    user_id: str
    add: bool


@dataclass
class GuildRoster:
    """What one guild wants on its LLDAP server."""

    instance_key: str
    # group id → Discord ids of the guild's members holding a role mapped to it
    holders: dict[int, set[str]] = field(default_factory=dict)
    blocked: set[int] = field(default_factory=set)


@dataclass
class SyncReport:
    added: int = 0
    removed: int = 0
    failed: int = 0
    blocked_groups: set[int] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def parse_role_groups(raw: Any) -> tuple[dict[int, int], list[str]]:
    """``{"<role id>": "<group id>"}`` from the dashboard → ints, plus the invalid entries."""
    mapping: dict[int, int] = {}
    invalid: list[str] = []
    if not isinstance(raw, Mapping):
        return mapping, invalid
    for key, value in raw.items():
        role, group = str(key).strip(), str(value).strip()
        if role.isdigit() and group.isdigit():
            mapping[int(role)] = int(group)
        else:
            invalid.append(f"{key} → {value}")
    return mapping, invalid


def merge_rosters(
    rosters: Iterable[GuildRoster],
) -> tuple[dict[str, dict[int, set[str]]], dict[str, set[int]]]:
    """Per server: the wanted members of each syncable group, and the blocked groups."""
    holders: dict[str, dict[int, set[str]]] = {}
    blocked: dict[str, set[int]] = {}
    for roster in rosters:
        server = holders.setdefault(roster.instance_key, {})
        for group_id, discord_ids in roster.holders.items():
            server.setdefault(group_id, set()).update(discord_ids)
        blocked.setdefault(roster.instance_key, set()).update(roster.blocked)
    desired = {
        key: {group: ids for group, ids in groups.items() if group not in blocked[key]}
        for key, groups in holders.items()
    }
    return desired, {key: groups for key, groups in blocked.items() if groups}


def plan_sync(users: Iterable[LldapUser], desired: Mapping[int, Set[str]]) -> list[GroupChange]:
    """The group additions and removals that make LLDAP match *desired*.

    Only the groups in *desired* are considered, and only linked users.
    """
    changes: list[GroupChange] = []
    for user in sorted(users, key=lambda u: u.user_id):
        if not user.discord_id:
            continue
        for group_id in sorted(desired):
            wanted = user.discord_id in desired[group_id]
            present = group_id in user.groups
            if wanted != present:
                changes.append(GroupChange(group_id, user.user_id, add=wanted))
    return changes


def describe_report(report: SyncReport) -> str:
    """French summary of a sync run, for the admin who triggered it."""
    lines = [
        f"Synchronisation LLDAP terminée : {report.added} ajout(s), "
        f"{report.removed} retrait(s), {report.failed} échec(s)."
    ]
    if report.blocked_groups:
        groups = ", ".join(str(group) for group in sorted(report.blocked_groups))
        lines.append(
            f"Groupes ignorés (serveur ou rôle introuvable, membres non chargés) : {groups}"
        )
    lines.extend(f"⚠️ {error}" for error in report.errors)
    return "\n".join(lines)
