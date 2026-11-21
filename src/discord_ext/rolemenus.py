"""Discord embed and component builders for reaction-role menus.

Shared by the slash-command extension (``extensions/reactionroles``) and the
WebUI route (``src/webui/routes/rolemenus.py``) so both render menus
identically. Lives in ``src/discord_ext`` because it builds interactions.py
objects — ``features/reactionroles`` stays free of Discord imports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from interactions import ActionRow, Button, ButtonStyle, Embed, StringSelectMenu, StringSelectOption

from features.reactionroles.permissions import MenuAuthor, RoleInfo, role_menu_refusal
from src.discord_ext.look import card

if TYPE_CHECKING:
    from features.reactionroles.models import RoleMenuEntry

# Discord caps action rows at 5 buttons each, max 5 rows per message.
MAX_ENTRIES = 25

# Persistent button namespace: rrole:{menu_id}:{entry_idx}
BUTTON_PREFIX = "rrole"
# A drop-down menu: rrolesel:{menu_id}, and its "remove my roles" button rroleclr:{menu_id}.
SELECT_PREFIX = "rrolesel"
CLEAR_PREFIX = "rroleclr"


def build_embed(
    title: str,
    description: str | None,
    entries: list[RoleMenuEntry],
    *,
    max_roles: int | None = None,
) -> Embed:
    """Render a role-menu embed (title, optional description, role/emoji/label list)."""
    body_lines = [
        " ".join(part for part in (e.emoji, f"<@&{e.role_id}>") if part) + f" — {e.label}"
        for e in entries
    ]
    embed_description = (description + "\n\n" if description else "") + "\n".join(body_lines)
    if max_roles == 1:
        embed_description += "\n\n*Un seul rôle à la fois : en choisir un retire l'autre.*"
    elif max_roles:
        embed_description += f"\n\n*{max_roles} rôles au plus à la fois.*"
    return card("moduleReactionRoles", title=title, description=embed_description)


def build_components(
    menu_id: str,
    entries: list[RoleMenuEntry],
    *,
    style: str = "buttons",
    max_roles: int | None = None,
) -> list[ActionRow]:
    """The menu's components: a button per role, or one drop-down (*style* ``"select"``)."""
    if style == "select":
        options = [
            StringSelectOption(label=e.label[:100], value=str(idx), emoji=e.emoji or None)
            for idx, e in enumerate(entries)
        ]
        return [
            ActionRow(
                StringSelectMenu(
                    *options,
                    placeholder="Choisis tes rôles",
                    min_values=1,
                    max_values=min(max_roles or len(options), len(options)),
                    custom_id=f"{SELECT_PREFIX}:{menu_id}",
                )
            ),
            ActionRow(
                Button(
                    label="Retirer mes rôles",
                    style=ButtonStyle.SECONDARY,
                    custom_id=f"{CLEAR_PREFIX}:{menu_id}",
                )
            ),
        ]
    rows: list[ActionRow] = []
    for chunk_start in range(0, len(entries), 5):
        buttons = []
        for offset, entry in enumerate(entries[chunk_start : chunk_start + 5]):
            idx = chunk_start + offset
            buttons.append(
                Button(
                    label=entry.label[:80],
                    style=ButtonStyle.SECONDARY,
                    emoji=entry.emoji or None,
                    custom_id=f"{BUTTON_PREFIX}:{menu_id}:{idx}",
                )
            )
        rows.append(ActionRow(*buttons))
    return rows


def render_menu(menu: Any, menu_id: str | None = None) -> dict[str, Any]:
    """The ``embeds`` and ``components`` of a stored menu, as Discord shows it."""
    return {
        "embeds": [
            build_embed(menu.title, menu.description, menu.entries, max_roles=menu.max_roles)
        ],
        "components": build_components(
            menu_id or menu.id, menu.entries, style=menu.style, max_roles=menu.max_roles
        ),
    }


# ── Permission checks (see features/reactionroles/permissions.py) ─────


def role_info(role: Any, guild_id: Any) -> RoleInfo:
    """Translate an ``interactions`` role into the plain record the rules read."""
    return RoleInfo(
        id=str(role.id),
        position=int(getattr(role, "position", 0) or 0),
        permissions=int(getattr(role, "permissions", 0) or 0),
        managed=bool(getattr(role, "managed", False)),
        is_default=str(role.id) == str(guild_id),
        mentionable=bool(getattr(role, "mentionable", False)),
    )


def menu_author(guild: Any, member: Any, *, bypass_hierarchy: bool = False) -> MenuAuthor:
    """Describe whoever builds the menu; *member* is ``None`` if not in the guild."""
    if member is None:
        return MenuAuthor(permissions=0, top_position=0, bypass_hierarchy=bypass_hierarchy)
    return MenuAuthor(
        permissions=int(member.guild_permissions),
        top_position=int(member.top_role.position),
        is_owner=bool(guild.is_owner(member.id)),
        bypass_hierarchy=bypass_hierarchy,
    )


async def bot_top_position(bot: Any, guild: Any) -> int:
    """Position of the bot's highest role in *guild* (0 if it can't be resolved)."""
    me = guild.me or await guild.fetch_member(bot.user.id)
    return int(me.top_role.position) if me is not None else 0


def entries_refusal(
    guild: Any, entries: list[RoleMenuEntry], *, author: MenuAuthor, bot_position: int
) -> str | None:
    """First reason one of *entries* can't go in the menu, or ``None`` if all can.

    An entry whose role is missing from *guild* is refused as well.
    """
    roles = {str(r.id): r for r in getattr(guild, "roles", []) or []}
    for idx, entry in enumerate(entries, 1):
        role = roles.get(str(entry.role_id))
        if role is None:
            return f"Entrée {idx} : rôle <@&{entry.role_id}> introuvable."
        refusal = role_menu_refusal(
            role_info(role, guild.id), author=author, bot_top_position=bot_position
        )
        if refusal:
            return f"Entrée {idx} : {refusal}"
    return None
