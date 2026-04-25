"""Discord embed and component builders for role menus.

Lives in ``features/`` (not ``extensions/``) so the slash-command extension
and the WebUI route can share the exact same renderer without crossing
package boundaries.
"""

from interactions import ActionRow, Button, ButtonStyle, Embed

from features.reactionroles.models import RoleMenuEntry
from src.discord_ext.embeds import Colors

# Discord caps action rows at 5 buttons each, max 5 rows per message.
MAX_ENTRIES = 25

# Persistent button namespace: rrole:{menu_id}:{entry_idx}
BUTTON_PREFIX = "rrole"


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
