"""The "❓ Que faire ici ?" button a module can put under a message.

It opens ``/aide``'s overview for whoever clicks it, privately; the help
extension answers it, so a module only needs the button.
"""

from interactions import Button, ButtonStyle

HELP_BUTTON_ID = "aide:ouvrir"


def help_button(label: str = "Que faire ici ?") -> Button:
    return Button(style=ButtonStyle.SECONDARY, label=label, emoji="❓", custom_id=HELP_BUTTON_ID)


__all__ = ["HELP_BUTTON_ID", "help_button"]
