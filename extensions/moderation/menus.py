"""Right-click a member: 🛡️ Historique and ⚠️ Avertir… (B1), for moderators.

Both are hidden from members without "Exclure temporairement des membres"
(``default_member_permissions``) and check it again when they run, since a
server can loosen the default. "Avertir…" asks for the reason in a form and
then does what ``/avertir`` does.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from interactions import (
    ContextMenuContext,
    Embed,
    Modal,
    ModalContext,
    ParagraphText,
    Permissions,
    modal_callback,
    user_context_menu,
)
from interactions.client.errors import NotFound

from src.discord_ext.menus import menu_help
from src.discord_ext.messages import (
    guild_id_of,
    guild_of,
    permission_names,
    send_error,
    send_success,
)
from src.discord_ext.scopes import guild_scopes

from ._common import MODERATION, can_moderate
from .commands import MAX_REASON_LENGTH

if TYPE_CHECKING:
    from features.moderation import Infraction, ModerationRepository, ReportRepository

HISTORY_MENU = "🛡️ Historique"
WARN_MENU = "⚠️ Avertir…"
WARN_FORM = "modwarn"  # modwarn:{user_id}
REASON_FIELD = "reason"
_WARN_FORM_RE = re.compile(rf"^{WARN_FORM}:(\d+)$")

NEEDS_MODERATE = (
    f"Il te faut la permission {permission_names(Permissions.MODERATE_MEMBERS)} sur ce serveur."
)


def warn_form(user_id: Any, name: str) -> Modal:
    return Modal(
        ParagraphText(
            label="Raison (envoyée au membre)",
            custom_id=REASON_FIELD,
            placeholder="Facultatif, mais ça aide le membre à comprendre.",
            required=False,
            max_length=MAX_REASON_LENGTH,
        ),
        # Discord caps a form's title at 45 characters.
        title=f"Avertir {name}"[:45],
        custom_id=f"{WARN_FORM}:{user_id}",
    )


class MenusMixin:
    """User context menus for moderators."""

    if TYPE_CHECKING:

        def repository(self, guild_id: str | int) -> ModerationRepository: ...

        def reports(self, guild_id: str | int) -> ReportRepository: ...

        def _build_history_embed(self, member: Any, items: list[Infraction]) -> Embed: ...

        async def warn_member(
            self, ctx: Any, settings: dict, member: Any, reason: str | None
        ) -> tuple[Infraction, int]: ...

    @menu_help("Les sanctions et signalements de ce membre (modérateurs)")
    @user_context_menu(
        name=HISTORY_MENU,
        scopes=guild_scopes(MODERATION.guild_ids()),
        default_member_permissions=Permissions.MODERATE_MEMBERS,
    )
    async def history_menu(self, ctx: ContextMenuContext) -> None:
        author: Any = ctx.author
        if not author.has_permission(Permissions.MODERATE_MEMBERS):
            await send_error(ctx, NEEDS_MODERATE, icon="🔒")
            return
        member: Any = ctx.target
        guild_id = guild_id_of(ctx)
        items = await self.repository(guild_id).list_for_user(str(member.id))
        reported = await self.reports(guild_id).count_against(str(member.id))
        reports_line = (
            f"🚩 {reported} message{'s' if reported > 1 else ''} signalé{'s' if reported > 1 else ''}"
            if reported
            else ""
        )
        if not items:
            text = f"Aucune sanction enregistrée pour {member.mention}."
            await send_success(ctx, f"{text}\n{reports_line}" if reports_line else text)
            return
        embed = self._build_history_embed(member, items)
        if reports_line:
            embed.description = f"{embed.description}\n{reports_line}"
        await ctx.send(embeds=[embed], ephemeral=True)

    @menu_help("Avertir ce membre, avec une raison (modérateurs)")
    @user_context_menu(
        name=WARN_MENU,
        scopes=guild_scopes(MODERATION.guild_ids()),
        default_member_permissions=Permissions.MODERATE_MEMBERS,
    )
    async def warn_menu(self, ctx: ContextMenuContext) -> None:
        member: Any = ctx.target
        refusal = self._warn_refusal(ctx, member)
        if refusal:
            await send_error(ctx, refusal, icon="🔒")
            return
        name = getattr(member, "display_name", None) or getattr(member, "username", "ce membre")
        await ctx.send_modal(warn_form(member.id, str(name)))

    @modal_callback(_WARN_FORM_RE)
    async def on_warn_form(self, ctx: ModalContext, **responses: str) -> None:
        match = _WARN_FORM_RE.match(ctx.custom_id)
        if not match:
            return
        guild: Any = guild_of(ctx)
        try:
            member = await guild.fetch_member(int(match.group(1)))
        except NotFound:
            member = None
        if member is None:
            await send_error(ctx, "Ce membre n'est plus sur le serveur.")
            return
        refusal = self._warn_refusal(ctx, member)
        if refusal:
            await send_error(ctx, refusal, icon="🔒")
            return
        await ctx.defer(ephemeral=True)
        reason = (responses.get(REASON_FIELD) or "").strip() or None
        settings = MODERATION.get(guild_id_of(ctx))
        infraction, count = await self.warn_member(ctx, settings, member, reason)
        await send_success(
            ctx,
            f"{member.mention} averti (cas **#{infraction.id}**). "
            f"Avertissements actifs : **{count}**.",
        )

    @staticmethod
    def _warn_refusal(ctx: Any, member: Any) -> str | None:
        if not ctx.author.has_permission(Permissions.MODERATE_MEMBERS):
            return NEEDS_MODERATE
        if getattr(member, "guild", None) is None and not hasattr(member, "roles"):
            return "Ce membre n'est plus sur le serveur."
        return can_moderate(ctx.author, member, getattr(guild_of(ctx), "me", None))


__all__ = ["MenusMixin"]
