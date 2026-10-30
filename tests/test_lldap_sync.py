"""Role → LLDAP group sync planning."""

from features.lldap import (
    GroupChange,
    GuildRoster,
    SyncReport,
    describe_report,
    merge_rosters,
    parse_role_groups,
    plan_sync,
)
from src.integrations.lldap import LldapUser


def _user(user_id: str, discord_id: str | None, *groups: int) -> LldapUser:
    return LldapUser(user_id=user_id, discord_id=discord_id, groups=frozenset(groups))


def test_parse_role_groups_keeps_valid_entries():
    mapping, invalid = parse_role_groups({"111": "3", " 222 ": "4 ", "abc": "5", "333": "x"})
    assert mapping == {111: 3, 222: 4}
    assert invalid == ["abc → 5", "333 → x"]


def test_parse_role_groups_tolerates_missing_config():
    assert parse_role_groups(None) == ({}, [])
    assert parse_role_groups(["111"]) == ({}, [])


def test_plan_sync_adds_role_holders_and_removes_the_others():
    users = [
        _user("alice", "1"),  # holds the role, not in the group yet
        _user("bob", "2", 3),  # lost the role
        _user("carol", "3", 3),  # holds the role, already in
    ]
    changes = plan_sync(users, {3: {"1", "3"}})
    assert changes == [
        GroupChange(group_id=3, user_id="alice", add=True),
        GroupChange(group_id=3, user_id="bob", add=False),
    ]


def test_plan_sync_leaves_unlinked_users_and_unmapped_groups_alone():
    users = [
        _user("admin", None, 3, 4),  # no Discord id: never touched
        _user("dave", "4", 9),  # group 9 isn't mapped: kept
    ]
    assert plan_sync(users, {3: set(), 4: set()}) == []


def test_plan_sync_handles_several_groups():
    users = [_user("erin", "5", 3)]
    assert plan_sync(users, {3: set(), 4: {"5"}}) == [
        GroupChange(group_id=3, user_id="erin", add=False),
        GroupChange(group_id=4, user_id="erin", add=True),
    ]


def test_merge_rosters_unions_guilds_sharing_a_server():
    desired, blocked = merge_rosters(
        [
            GuildRoster("https://lldap.a", holders={3: {"1"}}),
            GuildRoster("https://lldap.a", holders={3: {"2"}, 4: {"2"}}),
        ]
    )
    assert desired == {"https://lldap.a": {3: {"1", "2"}, 4: {"2"}}}
    assert blocked == {}
    # Neither guild's holder is removed by the other's view.
    users = [_user("one", "1", 3), _user("two", "2", 3, 4)]
    assert plan_sync(users, desired["https://lldap.a"]) == []


def test_merge_rosters_keeps_servers_apart():
    desired, _ = merge_rosters(
        [
            GuildRoster("https://lldap.a", holders={3: {"1"}}),
            GuildRoster("https://lldap.b", holders={3: {"2"}}),
        ]
    )
    assert desired == {"https://lldap.a": {3: {"1"}}, "https://lldap.b": {3: {"2"}}}


def test_merge_rosters_drops_a_group_blocked_by_any_guild():
    desired, blocked = merge_rosters(
        [
            GuildRoster("https://lldap.a", holders={3: {"1"}, 4: {"1"}}),
            GuildRoster("https://lldap.a", blocked={3}),
        ]
    )
    assert desired == {"https://lldap.a": {4: {"1"}}}
    assert blocked == {"https://lldap.a": {3}}


def test_describe_report():
    text = describe_report(
        SyncReport(
            added=2, removed=1, failed=0, blocked_groups={4, 3}, errors=["LLDAP injoignable"]
        )
    )
    assert "2 ajout(s), 1 retrait(s), 0 échec(s)" in text
    assert "3, 4" in text
    assert "⚠️ LLDAP injoignable" in text
