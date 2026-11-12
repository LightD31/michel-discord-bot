"""Persistent button callback that toggles the role on click."""

import re

from interactions import ComponentContext, component_callback

from features.reactionroles import self_assign_refusal
from src.core.logging import log_failure
from src.discord_ext.messages import send_error
from src.discord_ext.rolemenus import role_info

from ._common import BUTTON_PREFIX, logger

_CUSTOM_ID_RE = re.compile(rf"^{BUTTON_PREFIX}:([a-f0-9]{{24}}):(\d+)$")


class ButtonsMixin:
    """Handle clicks on role-menu buttons (toggle role on/off)."""

    @component_callback(_CUSTOM_ID_RE)
    async def on_role_button(self, ctx: ComponentContext) -> None:
        if not ctx.guild:
            await send_error(ctx, "Cette action n'est disponible qu'en serveur.")
            return

        match = _CUSTOM_ID_RE.match(ctx.custom_id)
        if not match:
            return
        menu_id, entry_idx_str = match.group(1), match.group(2)
        entry_idx = int(entry_idx_str)

        menu = await self.repository(ctx.guild_id).get(menu_id)
        if not menu:
            await send_error(ctx, "Ce menu n'existe plus.")
            return
        if entry_idx >= len(menu.entries):
            await send_error(ctx, "Cette entrée n'existe plus dans le menu.")
            return

        role_id = int(menu.entries[entry_idx].role_id)
        member = ctx.author
        try:
            has_role = any(int(getattr(r, "id", r)) == role_id for r in member.roles)
        except Exception:
            has_role = False

        if not has_role:
            # Re-checked on every click: a role can gain moderation permissions
            # after the menu was posted. Removing one is always allowed.
            role = ctx.guild.get_role(role_id)
            refusal = self_assign_refusal(role_info(role, ctx.guild_id)) if role else None
            if refusal:
                logger.warning(
                    "Refused self-assigning privileged role %s to %s in guild %s (menu %s)",
                    role_id,
                    ctx.author.id,
                    ctx.guild_id,
                    menu_id,
                )
                await send_error(ctx, refusal)
                return

        try:
            if has_role:
                await member.remove_role(role_id, reason="Reaction-roles toggle")
                await ctx.send(f"Rôle <@&{role_id}> retiré ❌", ephemeral=True)
                logger.info(
                    "Removed role %s from %s in guild %s", role_id, ctx.author.id, ctx.guild_id
                )
            else:
                await member.add_role(role_id, reason="Reaction-roles toggle")
                await ctx.send(f"Rôle <@&{role_id}> ajouté ✅", ephemeral=True)
                logger.info("Added role %s to %s in guild %s", role_id, ctx.author.id, ctx.guild_id)
        except Exception as e:
            log_failure(logger, "Failed to toggle role %s for %s: %s", role_id, ctx.author.id, e)
            await send_error(ctx, "Impossible de modifier ce rôle (permissions ou hiérarchie ?).")
