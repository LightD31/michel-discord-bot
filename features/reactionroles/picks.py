"""What a click or a pick in a role menu changes: the roles to add and to remove (B3).

Pure: the extension reads the member's roles, applies the plan, and checks
each role it adds against :func:`features.reactionroles.permissions.self_assign_refusal`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plan:
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    refusal: str | None = None


def button_plan(menu_roles: list[str], held: set[str], clicked: str, max_roles: int | None) -> Plan:
    """A click on *clicked*'s button: take it back if held, else give it within *max_roles*.

    With one role at a time (*max_roles* 1) the new one replaces the old one;
    with a higher limit a member at it is told to remove one first.
    """
    if clicked in held:
        return Plan(remove=[clicked])
    mine = [role for role in menu_roles if role in held]
    if max_roles == 1:
        return Plan(add=[clicked], remove=mine)
    if max_roles and len(mine) >= max_roles:
        return Plan(
            refusal=f"Tu as déjà {len(mine)} rôles de ce menu, le maximum : retires-en un d'abord."
        )
    return Plan(add=[clicked])


def select_plan(menu_roles: list[str], held: set[str], picked: list[str]) -> Plan:
    """A pick in the drop-down: the member ends up with exactly the menu's roles *picked*."""
    wanted = {role for role in picked if role in menu_roles}
    return Plan(
        add=[role for role in menu_roles if role in wanted and role not in held],
        remove=[role for role in menu_roles if role in held and role not in wanted],
    )


__all__ = ["Plan", "button_plan", "select_plan"]
