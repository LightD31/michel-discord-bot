"""Config schema, logger, shared constants, and embed/component builders.

The builders are kept here (not in ``commands.py``) so the WebUI builder can
reuse the exact same Discord rendering as ``/rolemenu create``.
"""

import os

from interactions import ActionRow, Button, ButtonStyle, Embed

from features.reactionroles import RoleMenuEntry
from src.core import logging as logutil
from src.core.config import load_config
from src.discord_ext.embeds import Colors
from src.webui.schemas import SchemaBase, enabled_field, register_module

logger = logutil.init_logger(os.path.basename(__file__))


@register_module("moduleReactionRoles")
class ReactionRolesConfig(SchemaBase):
    __label__ = "Rôles à boutons"
    __description__ = "Menus de rôles auto-attribués via boutons persistants."
    __icon__ = "🎭"
    __category__ = "Communauté"

    enabled: bool = enabled_field()


_, _, enabled_servers = load_config("moduleReactionRoles")
enabled_servers_int = [int(s) for s in enabled_servers]  # type: ignore[misc]

# Namespace for persistent button custom_ids: rrole:{menu_id}:{entry_idx}
BUTTON_PREFIX = "rrole"
# Discord caps action rows at 5 buttons each, max 5 rows per message.
MAX_ENTRIES = 25


def build_embed(title: str, description: str | None, entries: list[RoleMenuEntry]) -> Embed:
    """Render a role-menu embed (title, optional description, role/emoji/label list)."""
    body_lines = [f"{e.emoji} <@&{e.role_id}> — {e.label}" for e in entries]
    embed_description = (description + "\n\n" if description else "") + "\n".join(body_lines)
    return Embed(title=title, description=embed_description, color=Colors.UTIL)


def build_components(menu_id: str, entries: list[RoleMenuEntry]) -> list[ActionRow]:
    """Render the action rows of buttons whose ``custom_id`` toggles each role."""
    rows: list[ActionRow] = []
    for chunk_start in range(0, len(entries), 5):
        buttons = []
        for offset, entry in enumerate(entries[chunk_start : chunk_start + 5]):
            idx = chunk_start + offset
            buttons.append(
                Button(
                    label=entry.label[:80],
                    style=ButtonStyle.SECONDARY,
                    emoji=entry.emoji,
                    custom_id=f"{BUTTON_PREFIX}:{menu_id}:{idx}",
                )
            )
        rows.append(ActionRow(*buttons))
    return rows
