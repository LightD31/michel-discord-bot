"""Who counts as staff (``src/discord_ext/staff.py``), shared by moderation and suggestions."""

from types import SimpleNamespace

from interactions import Permissions

from src.discord_ext.staff import is_staff, top_role_position


class Member:
    def __init__(self, *roles, admin: bool = False) -> None:
        self.roles = [SimpleNamespace(id=r, position=p) for r, p in roles]
        self.admin = admin

    def has_permission(self, permission) -> bool:
        return self.admin and permission == Permissions.ADMINISTRATOR


def test_administrators_are_staff_without_a_staff_role():
    assert is_staff(Member(admin=True), None)
    assert not is_staff(Member((5, 1)), None)


def test_the_staff_role_makes_staff():
    member = Member((5, 1), (7, 3))
    assert is_staff(member, "7") and is_staff(member, 7)
    assert not is_staff(member, "8")


def test_a_malformed_staff_role_or_a_user_is_never_staff():
    assert not is_staff(Member((5, 1)), "not-an-id")
    assert not is_staff(SimpleNamespace(id=1), "5")  # a User: no roles, no permissions


def test_the_top_role_position():
    assert top_role_position(Member((5, 1), (7, 3))) == 3
    assert top_role_position(SimpleNamespace()) == 0
