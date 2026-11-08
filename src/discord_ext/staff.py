"""Who counts as staff, and where a member sits in the role hierarchy.

Shared by the modules that let a configured staff role do what an
administrator can (moderation, suggestions). Works on anything shaped like an
``interactions.Member``; a ``User`` (a DM) is never staff.
"""

from __future__ import annotations

from typing import Any

from interactions import Permissions


def _as_id(value: object) -> int | None:
    try:
        return int(getattr(value, "id", value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None


def is_staff(member: Any, staff_role_id: object) -> bool:
    """Administrators, and the members holding *staff_role_id* (unset: admins only)."""
    has_permission = getattr(member, "has_permission", None)
    if callable(has_permission) and has_permission(Permissions.ADMINISTRATOR):
        return True
    wanted = _as_id(staff_role_id) if staff_role_id else None
    if wanted is None:
        return False
    return any(_as_id(role) == wanted for role in getattr(member, "roles", None) or [])


def top_role_position(member: Any) -> int:
    """Highest position among *member*'s roles, 0 without any."""
    positions = [getattr(r, "position", 0) or 0 for r in getattr(member, "roles", None) or []]
    return max(positions, default=0)


__all__ = ["is_staff", "top_role_position"]
