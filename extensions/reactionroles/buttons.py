"""A member picking roles in a menu: a button click, a drop-down pick, "Retirer mes rôles"."""

import re
from typing import TYPE_CHECKING, Any

from interactions import ComponentContext, component_callback

from features.reactionroles import self_assign_refusal
from features.reactionroles.picks import Plan, button_plan, select_plan
from src.core.logging import log_failure
from src.discord_ext.messages import send_error
from src.discord_ext.rolemenus import CLEAR_PREFIX, SELECT_PREFIX, role_info

from ._common import BUTTON_PREFIX, logger

_CUSTOM_ID_RE = re.compile(rf"^{BUTTON_PREFIX}:([a-f0-9]{{24}}):(\d+)$")
_SELECT_RE = re.compile(rf"^{SELECT_PREFIX}:([a-f0-9]{{24}})$")
_CLEAR_RE = re.compile(rf"^{CLEAR_PREFIX}:([a-f0-9]{{24}})$")


def _held(member: Any) -> set[str]:
    return {str(getattr(r, "id", r)) for r in getattr(member, "roles", None) or []}


class ButtonsMixin:
    """Give and take the roles of a menu, by button or by drop-down."""

    if TYPE_CHECKING:

        def repository(self, guild_id: str | int) -> Any: ...

    async def _menu_for(self, ctx: ComponentContext, menu_id: str) -> Any:
        if not ctx.guild:
            await send_error(ctx, "Cette action n'est disponible qu'en serveur.")
            return None
        menu = await self.repository(ctx.guild_id).get(menu_id)
        if not menu:
            await send_error(ctx, "Ce menu n'existe plus.")
        return menu

    @component_callback(_CUSTOM_ID_RE)
    async def on_role_button(self, ctx: ComponentContext) -> None:
        match = _CUSTOM_ID_RE.match(ctx.custom_id)
        if not match:
            return
        menu = await self._menu_for(ctx, match.group(1))
        if menu is None:
            return
        entry_idx = int(match.group(2))
        if entry_idx >= len(menu.entries):
            await send_error(ctx, "Cette entrée n'existe plus dans le menu.")
            return
        menu_roles = [e.role_id for e in menu.entries]
        plan = button_plan(
            menu_roles, _held(ctx.author), menu.entries[entry_idx].role_id, menu.max_roles
        )
        await self._apply(ctx, match.group(1), plan)

    @component_callback(_SELECT_RE)
    async def on_role_select(self, ctx: ComponentContext) -> None:
        match = _SELECT_RE.match(ctx.custom_id)
        if not match:
            return
        menu = await self._menu_for(ctx, match.group(1))
        if menu is None:
            return
        picked = [
            menu.entries[int(v)].role_id
            for v in ctx.values or []
            if str(v).isdigit() and int(v) < len(menu.entries)
        ][: menu.max_roles or None]
        plan = select_plan([e.role_id for e in menu.entries], _held(ctx.author), picked)
        await self._apply(ctx, match.group(1), plan)

    @component_callback(_CLEAR_RE)
    async def on_role_clear(self, ctx: ComponentContext) -> None:
        match = _CLEAR_RE.match(ctx.custom_id)
        if not match:
            return
        menu = await self._menu_for(ctx, match.group(1))
        if menu is None:
            return
        plan = select_plan([e.role_id for e in menu.entries], _held(ctx.author), [])
        await self._apply(ctx, match.group(1), plan)

    async def _apply(self, ctx: ComponentContext, menu_id: str, plan: Plan) -> None:
        """Give and take the roles *plan* names, then say what changed."""
        if plan.refusal:
            await send_error(ctx, plan.refusal)
            return
        for role_id in plan.add:
            # Re-checked on every pick: a role can gain moderation permissions
            # after the menu was posted. Removing one is always allowed.
            role = ctx.guild.get_role(int(role_id))
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
        member = ctx.author
        try:
            for role_id in plan.remove:
                await member.remove_role(int(role_id), reason="Menu de rôles")
            for role_id in plan.add:
                await member.add_role(int(role_id), reason="Menu de rôles")
        except Exception as e:
            log_failure(logger, "Failed to change roles of %s (menu %s): %s", member.id, menu_id, e)
            await send_error(
                ctx,
                "Je n'ai pas pu changer ce rôle : mon rôle doit être au-dessus de lui. "
                "Un administrateur peut corriger l'ordre des rôles.",
            )
            return
        lines = [f"Rôle <@&{r}> ajouté ✅" for r in plan.add]
        lines += [f"Rôle <@&{r}> retiré ❌" for r in plan.remove]
        await ctx.send(
            "\n".join(lines) or "Rien n'a changé : tu as déjà ces rôles.", ephemeral=True
        )
        logger.info(
            "Role menu %s: +%s -%s for %s in guild %s",
            menu_id,
            plan.add,
            plan.remove,
            member.id,
            ctx.guild_id,
        )
