"""🚩 Signaler: members report a message, moderators handle it from one card (A7).

Right-click a message → Applications → "🚩 Signaler" opens a form asking why.
The report lands on a card in the mod-log channel: the message quoted (its
text is never stored, only shown there), a link to it, who reported it and
why, and four buttons. Each button acts with the rights the moderator holds
on Discord: 🗑️ needs "Gérer les messages" in the message's channel, ⚠️ and
🔇 "Exclure temporairement des membres" plus a role above the author's, ✅
either. The report is claimed before the action, so two moderators clicking
at once act once; an action Discord refuses reopens it. Every report of the
same message raises the counter on the same card. Members who reported it are
thanked by DM once it is handled (``notifyReporters``), without the outcome.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import TYPE_CHECKING, Any

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    ContextMenuContext,
    Embed,
    Modal,
    ModalContext,
    ParagraphText,
    Permissions,
    Timestamp,
    component_callback,
    message_context_menu,
    modal_callback,
)
from interactions.client.errors import NotFound

from features.moderation import MessageReport, ReportLimiter, ReportRepository, ReportStatus
from features.moderation.reports import MAX_REASON_LENGTH
from features.userdata import NOBODY
from src.core import clock
from src.core.text import EMBED_FIELD_VALUE_LIMIT, clip
from src.discord_ext.channels import fetch_guild_channel, member_can
from src.discord_ext.errors import failure_reply
from src.discord_ext.look import card
from src.discord_ext.menus import menu_help
from src.discord_ext.messages import (
    CAN_PURGE,
    edit_message_if_changed,
    fetch_user_safe,
    guild_id_of,
    guild_of,
    jump_url,
    permission_names,
    send_error,
    send_success,
)
from src.discord_ext.scopes import guild_scopes

from ._common import MODERATION, can_moderate, logger
from .commands import ActionRefused

if TYPE_CHECKING:
    from datetime import datetime

    from features.moderation import Infraction, InfractionType

REPORT_MENU = "🚩 Signaler"
REPORT_FORM = "report_form"  # report_form:{channel_id}:{message_id}
CARD = "report"  # report:{action}:{message_id}
ACTION_FORM = "report_reason"  # report_reason:{warn|timeout}:{message_id}
REASON_FIELD = "reason"
QUOTE_LIMIT = 1000
SHOWN_REPORTERS = 5
REPORT_TIMEOUT_SECONDS = 600

_FORM_RE = re.compile(rf"^{REPORT_FORM}:(\d+):(\d+)$")
_CARD_RE = re.compile(rf"^{CARD}:(delete|warn|timeout|dismiss):(\d+)$")
_ACTION_FORM_RE = re.compile(rf"^{ACTION_FORM}:(warn|timeout):(\d+)$")

ACTION_STATUS: dict[str, ReportStatus] = {
    "delete": "deleted",
    "warn": "warned",
    "timeout": "timed_out",
    "dismiss": "dismissed",
}
STATUS_LINES: dict[str, str] = {
    "open": "🟥 À traiter",
    "deleted": "🗑️ Message supprimé par {by}",
    "warned": "⚠️ Auteur averti par {by}",
    "timed_out": "🔇 Auteur exclu 10 min par {by}",
    "dismissed": "✅ Classé sans suite par {by}",
}
DONE: dict[str, str] = {
    "deleted": "🗑️ Message supprimé, signalement clos.",
    "warned": "⚠️ Auteur averti, signalement clos.",
    "timed_out": "🔇 Auteur exclu 10 minutes, signalement clos.",
    "dismissed": "✅ Signalement classé sans suite.",
}
THANKS = (
    "🚩 Merci, c'est transmis aux modérateurs. Eux seuls savent que le signalement vient de toi."
)
ALREADY = "Tu as déjà signalé ce message : les modérateurs l'ont reçu."
SLOW_DOWN = "Tu as signalé plusieurs messages coup sur coup : patiente quelques minutes."
HANDLED_DM = "🚩 Merci pour ton signalement sur **{guild}** : les modérateurs l'ont traité."
NO_MODLOG = (
    "Les signalements ne sont pas encore branchés ici : un administrateur doit choisir le "
    "« Salon des logs de modération » dans le tableau de bord."
)
CAN_SANCTION = (
    f"Il te faut la permission {permission_names(Permissions.MODERATE_MEMBERS)} pour sanctionner."
)
CAN_DISMISS = (
    f"Il te faut la permission {permission_names(Permissions.MANAGE_MESSAGES)} ou "
    f"{permission_names(Permissions.MODERATE_MEMBERS)} pour classer un signalement."
)


def quote_of(content: str | None, attachments: int = 0) -> str:
    """The message as the card quotes it: each line behind ``>``, clipped, attachments counted."""
    text = (content or "").strip()
    lines = [f"> {line}" for line in clip(text, QUOTE_LIMIT).splitlines()] if text else []
    quote = "\n".join(lines) or "*(message sans texte)*"
    if attachments:
        plural = "s" if attachments > 1 else ""
        quote += f"\n📎 {attachments} pièce{plural} jointe{plural}"
    return quote


def status_line(report: MessageReport) -> str:
    line = STATUS_LINES[report.status].format(by=f"<@{report.handled_by}>")
    if report.case_id:
        line += f" · cas #{report.case_id}"
    return line


def _reporter_lines(report: MessageReport) -> str:
    lines = [
        f"{'Membre anonymisé' if r.user_id == NOBODY else f'<@{r.user_id}>'} — "
        f"{clip(r.reason, 200)}"
        for r in report.reporters[:SHOWN_REPORTERS]
    ]
    more = len(report.reporters) - SHOWN_REPORTERS
    if more > 0:
        lines.append(f"… et {more} autre{'s' if more > 1 else ''}")
    return clip("\n".join(lines) or "—", EMBED_FIELD_VALUE_LIMIT)


def report_card(report: MessageReport, quote: str, guild_id: str) -> tuple[Embed, list[ActionRow]]:
    """The card in the mod-log channel: the message, who reported it, and what to do."""
    count = len(report.reporters)
    embed = card(
        "moduleModeration",
        "error" if report.is_open else "neutral",
        title="🚩 Message signalé" + (f" ×{count}" if count > 1 else ""),
        description=quote,
    )
    embed.add_field(
        name="Auteur", value=f"<@{report.author_id}> (`{report.author_id}`)", inline=True
    )
    embed.add_field(name="Salon", value=f"<#{report.channel_id}>", inline=True)
    embed.add_field(
        name="Signalé par" if count == 1 else f"Signalé par {count} membres",
        value=_reporter_lines(report),
        inline=False,
    )
    embed.add_field(name="Statut", value=status_line(report), inline=False)
    embed.timestamp = Timestamp.fromdatetime(report.created_at)
    link = Button(
        style=ButtonStyle.LINK,
        label="Aller au message",
        url=jump_url(guild_id, report.channel_id, report.message_id),
    )
    rows = [ActionRow(link)]
    if report.is_open:
        mid = report.message_id
        rows.append(
            ActionRow(
                Button(
                    style=ButtonStyle.DANGER,
                    label="Supprimer",
                    emoji="🗑️",
                    custom_id=f"{CARD}:delete:{mid}",
                ),
                Button(
                    style=ButtonStyle.SECONDARY,
                    label="Avertir",
                    emoji="⚠️",
                    custom_id=f"{CARD}:warn:{mid}",
                ),
                Button(
                    style=ButtonStyle.SECONDARY,
                    label="Exclure 10 min",
                    emoji="🔇",
                    custom_id=f"{CARD}:timeout:{mid}",
                ),
                Button(
                    style=ButtonStyle.SUCCESS,
                    label="Classer",
                    emoji="✅",
                    custom_id=f"{CARD}:dismiss:{mid}",
                ),
            )
        )
    return embed, rows


def report_form(channel_id: Any, message_id: Any) -> Modal:
    return Modal(
        ParagraphText(
            label="Qu'est-ce qui pose problème ?",
            custom_id=REASON_FIELD,
            placeholder="En quelques mots. Seuls les modérateurs le liront.",
            required=True,
            max_length=MAX_REASON_LENGTH,
        ),
        title="Signaler aux modérateurs",
        custom_id=f"{REPORT_FORM}:{channel_id}:{message_id}",
    )


def action_form(action: str, message_id: str) -> Modal:
    title = "Avertir l'auteur" if action == "warn" else "Exclure l'auteur 10 minutes"
    return Modal(
        ParagraphText(
            label="Raison (envoyée au membre)",
            custom_id=REASON_FIELD,
            placeholder="Facultatif. Ne dis pas qui a signalé le message.",
            required=False,
            max_length=MAX_REASON_LENGTH,
        ),
        title=title,
        custom_id=f"{ACTION_FORM}:{action}:{message_id}",
    )


async def _fetch_message(channel: Any, message_id: str) -> Any:
    """The message, or ``None`` when it is gone (the library answers 404 either way)."""
    try:
        return await channel.fetch_message(int(message_id))
    except NotFound:
        return None


class ReportsMixin:
    """The "🚩 Signaler" menu, its form, and the buttons of the card it posts."""

    bot: Client
    report_limits: ReportLimiter
    report_lock: asyncio.Lock

    if TYPE_CHECKING:

        def reports(self, guild_id: str | int) -> ReportRepository: ...

        async def warn_member(
            self, ctx: Any, settings: dict, member: Any, reason: str | None
        ) -> tuple[Infraction, int]: ...

        async def timeout_member(
            self, ctx: Any, settings: dict, member: Any, secs: int, reason: str | None
        ) -> Infraction: ...

        async def _record(
            self,
            ctx: Any,
            *,
            mtype: InfractionType,
            target_id: str | int,
            reason: str | None,
            duration_seconds: int | None = None,
            expires_at: datetime | None = None,
        ) -> Infraction: ...

        async def log_case(
            self,
            settings: dict,
            infraction: Infraction,
            *,
            target_name: str | None = None,
            moderator_name: str | None = None,
        ) -> None: ...

    # --- A member reports ---------------------------------------------------

    @menu_help("Signaler ce message aux modérateurs, en privé")
    @message_context_menu(name=REPORT_MENU, scopes=guild_scopes(MODERATION.guild_ids()))
    async def report_message(self, ctx: ContextMenuContext) -> None:
        message: Any = ctx.target
        guild_id = guild_id_of(ctx)
        settings = MODERATION.get(guild_id)
        if not settings.get("modLogChannelId"):
            await send_error(ctx, NO_MODLOG, icon="🚩")
            return
        author_id = str(getattr(getattr(message, "author", None), "id", ""))
        if author_id == str(ctx.author.id):
            await send_error(ctx, "Tu ne peux pas signaler ton propre message.")
            return
        existing = await self.reports(guild_id).get(str(message.id))
        if existing and any(r.user_id == str(ctx.author.id) for r in existing.reporters):
            await send_error(ctx, ALREADY, icon="🚩")
            return
        if self.report_limits.is_limited(guild_id, str(ctx.author.id), clock.now()):
            await send_error(ctx, SLOW_DOWN, icon="⏳")
            return
        await ctx.send_modal(report_form(ctx.channel_id, message.id))

    @modal_callback(_FORM_RE)
    async def on_report_form(self, ctx: ModalContext, **responses: str) -> None:
        match = _FORM_RE.match(ctx.custom_id)
        if not match:
            return
        channel_id, message_id = match.groups()
        guild_id = guild_id_of(ctx)
        settings = MODERATION.get(guild_id)
        if not settings.get("modLogChannelId"):
            await send_error(ctx, NO_MODLOG, icon="🚩")
            return
        reason = (responses.get(REASON_FIELD) or "").strip()
        if not reason:
            await send_error(ctx, "Écris en quelques mots ce qui pose problème.")
            return
        now = clock.now()
        if self.report_limits.is_limited(guild_id, str(ctx.author.id), now):
            await send_error(ctx, SLOW_DOWN, icon="⏳")
            return
        channel = await fetch_guild_channel(self.bot, guild_id, channel_id, logger=logger)
        message = await _fetch_message(channel, message_id) if channel is not None else None
        if message is None:
            await send_error(ctx, "Ce message n'existe plus : rien à signaler.")
            return
        await ctx.defer(ephemeral=True)
        report = await self.reports(guild_id).add(
            message_id=message_id,
            channel_id=channel_id,
            author_id=str(message.author.id),
            reporter_id=str(ctx.author.id),
            reason=reason,
            now=now,
        )
        if report is None:
            await send_error(ctx, ALREADY, icon="🚩")
            return
        self.report_limits.record(guild_id, str(ctx.author.id), now)
        logger.info("Message %s reported in guild %s", message_id, guild_id)
        await self.show_report(guild_id, settings, message_id, message=message)
        await ctx.send(THANKS, ephemeral=True)

    async def show_report(
        self, guild_id: str, settings: dict, message_id: str, *, message: Any = None
    ) -> None:
        """Post the report's card, or bring the one already posted up to date.

        One at a time (the bot is one process): the first reports of a message
        arriving together post one card, which the next ones edit.
        """
        repo = self.reports(guild_id)
        async with self.report_lock:
            report = await repo.get(message_id)
            if report is None:
                return
            card = await self._fetch_card(guild_id, report)
            if card is not None:
                quote = (card.embeds[0].description if card.embeds else None) or quote_of(None)
                embed, rows = report_card(report, quote, guild_id)
                await edit_message_if_changed(card, embeds=[embed], components=rows, logger=logger)
                return
            if message is not None:
                quote = quote_of(message.content, len(getattr(message, "attachments", None) or []))
            else:
                quote = "*(message plus disponible)*"
            channel = await fetch_guild_channel(
                self.bot, guild_id, str(settings.get("modLogChannelId") or ""), logger=logger
            )
            if channel is None or not hasattr(channel, "send"):
                logger.warning("Modlog channel of guild %s is unusable: report not shown", guild_id)
                return
            embed, rows = report_card(report, quote, guild_id)
            try:
                sent = await channel.send(embeds=[embed], components=rows)
            except Exception as e:
                logger.warning("Could not post the report card in guild %s: %s", guild_id, e)
                return
            await repo.set_card(message_id, str(channel.id), str(sent.id))

    async def _fetch_card(self, guild_id: str, report: MessageReport) -> Any:
        if not (report.card_channel_id and report.card_message_id):
            return None
        channel = await fetch_guild_channel(self.bot, guild_id, report.card_channel_id)
        if channel is None or not hasattr(channel, "fetch_message"):
            return None
        with contextlib.suppress(Exception):
            return await _fetch_message(channel, report.card_message_id)
        return None

    # --- A moderator handles it ---------------------------------------------

    @component_callback(_CARD_RE)
    async def on_report_button(self, ctx: ComponentContext) -> None:
        match = _CARD_RE.match(ctx.custom_id)
        if not match:
            return
        action, message_id = match.groups()
        report = await self._open_report(ctx, message_id)
        if report is None:
            return
        target, refusal = await self._refusal(ctx, action, report)
        if refusal:
            await send_error(ctx, refusal, icon="🔒")
            return
        if action in ("warn", "timeout"):
            await ctx.send_modal(action_form(action, message_id))
            return
        await ctx.defer(ephemeral=True)
        await self._handle(ctx, report, action, target, None)

    @modal_callback(_ACTION_FORM_RE)
    async def on_report_action_form(self, ctx: ModalContext, **responses: str) -> None:
        match = _ACTION_FORM_RE.match(ctx.custom_id)
        if not match:
            return
        action, message_id = match.groups()
        report = await self._open_report(ctx, message_id)
        if report is None:
            return
        # Checked again: the form may have stayed open a while.
        target, refusal = await self._refusal(ctx, action, report)
        if refusal:
            await send_error(ctx, refusal, icon="🔒")
            return
        await ctx.defer(ephemeral=True)
        reason = (responses.get(REASON_FIELD) or "").strip() or None
        await self._handle(ctx, report, action, target, reason)

    async def _open_report(self, ctx: Any, message_id: str) -> MessageReport | None:
        report = await self.reports(guild_id_of(ctx)).get(message_id)
        if report is None:
            await send_error(ctx, "Ce signalement n'existe plus.")
            return None
        if not report.is_open:
            await send_error(ctx, f"Déjà traité : {status_line(report)}.", icon="ℹ️")
            return None
        return report

    async def _refusal(
        self, ctx: Any, action: str, report: MessageReport
    ) -> tuple[Any, str | None]:
        """What the action works on, or why this moderator may not do it."""
        author = ctx.author
        if action == "dismiss":
            if author.has_permission(Permissions.MANAGE_MESSAGES) or author.has_permission(
                Permissions.MODERATE_MEMBERS
            ):
                return None, None
            return None, CAN_DISMISS
        if action == "delete":
            channel = await fetch_guild_channel(
                self.bot, guild_id_of(ctx), report.channel_id, logger=logger
            )
            if channel is None:
                return None, "Le salon de ce message n'existe plus : classe le signalement."
            if not member_can(author, channel, CAN_PURGE):
                return None, (
                    f"Il te faut la permission {permission_names(Permissions.MANAGE_MESSAGES)} "
                    f"dans <#{report.channel_id}>."
                )
            return channel, None
        if not author.has_permission(Permissions.MODERATE_MEMBERS):
            return None, CAN_SANCTION
        member = None
        with contextlib.suppress(NotFound):
            member = await guild_of(ctx).fetch_member(int(report.author_id))
        if member is None:
            return None, (
                "L'auteur n'est plus sur le serveur : supprime le message ou classe le signalement."
            )
        guard = can_moderate(author, member, getattr(guild_of(ctx), "me", None))
        return member, guard

    async def _handle(
        self, ctx: Any, report: MessageReport, action: str, target: Any, reason: str | None
    ) -> None:
        guild_id = guild_id_of(ctx)
        settings = MODERATION.get(guild_id)
        repo = self.reports(guild_id)
        status = ACTION_STATUS[action]
        claimed = await repo.claim(report.message_id, status, str(ctx.author.id), clock.now())
        if claimed is None:
            current = await repo.get(report.message_id)
            done = f" : {status_line(current)}" if current else ""
            await send_error(ctx, f"Un autre modérateur vient de s'en charger{done}.", icon="ℹ️")
            return
        try:
            case = await self._act(ctx, settings, claimed, action, target, reason)
        except ActionRefused as e:
            await repo.reopen(report.message_id, status)
            await send_error(ctx, failure_reply("Impossible d'agir", e.cause, logger))
            return
        if case is not None:
            await repo.set_case(report.message_id, case.id)
        await self.show_report(guild_id, settings, report.message_id)
        if settings.get("notifyReporters", True):
            await self._thank_reporters(ctx, claimed)
        logger.info(
            "Report of message %s in guild %s handled by %s: %s",
            report.message_id,
            guild_id,
            ctx.author.id,
            status,
        )
        await ctx.send(DONE[status], ephemeral=True)

    async def _act(
        self,
        ctx: Any,
        settings: dict,
        report: MessageReport,
        action: str,
        target: Any,
        reason: str | None,
    ) -> Infraction | None:
        if action == "dismiss":
            return None
        if action == "warn":
            infraction, _ = await self.warn_member(ctx, settings, target, reason)
            return infraction
        if action == "timeout":
            return await self.timeout_member(ctx, settings, target, REPORT_TIMEOUT_SECONDS, reason)
        message = await _fetch_message(target, report.message_id)
        if message is not None:
            try:
                await message.delete(reason=f"Signalement traité par {ctx.author.username}")
            except NotFound:
                pass
            except Exception as e:
                raise ActionRefused(e) from e
        channel_name = getattr(target, "name", report.channel_id)
        infraction = await self._record(
            ctx,
            mtype="note",
            target_id=report.author_id,
            reason=f"Message signalé supprimé dans #{channel_name}",
        )
        await self.log_case(
            settings,
            infraction,
            target_name=f"<@{report.author_id}>",
            moderator_name=ctx.author.mention,
        )
        return infraction

    async def _thank_reporters(self, ctx: Any, report: MessageReport) -> None:
        guild_name = getattr(guild_of(ctx), "name", "le serveur")
        for reporter in report.reporters:
            if reporter.user_id == NOBODY:
                continue
            _, user = await fetch_user_safe(self.bot, int(reporter.user_id))
            if user is None:
                continue
            with contextlib.suppress(Exception):  # closed DMs: the thanks is optional
                await user.send(HANDLED_DM.format(guild=guild_name))


__all__ = ["ReportsMixin", "quote_of", "report_card", "status_line"]
