"""What ``/aide`` says about a right-click menu (P4).

A context menu has a name but no description on Discord, so each one states
its own next to its decorator::

    @menu_help("Signaler ce message aux modérateurs, en privé")
    @message_context_menu(name="🚩 Signaler", scopes=...)

``tests/test_help_command.py`` fails on a menu without one.
"""

from __future__ import annotations

from collections.abc import Callable

from interactions import ContextMenu

MENU_HELP: dict[str, str] = {}


def menu_help(description: str) -> Callable[[ContextMenu], ContextMenu]:
    """Record *description* as the ``/aide`` line of the menu it decorates."""

    def record(menu: ContextMenu) -> ContextMenu:
        MENU_HELP[str(menu.name)] = description
        return menu

    return record


__all__ = ["MENU_HELP", "menu_help"]
