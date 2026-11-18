"""After the draw: anonymous messages, parcel marks, and the reminder before the date.

These buttons live in DMs, so every one re-reads the draw to find who the
clicker gives to or receives from: no custom id names a member (see
``src/discord_ext/secretsanta.py``). Nothing written through the relay is
stored; a reported message is read back from the DM it came in.
"""

import re
from datetime import datetime
from typing import Any

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    Embed,
    IntervalTrigger,
    Modal,
    ModalContext,
    ParagraphText,
    Task,
    component_callback,
    listen,
    modal_callback,
    spread_to_rows,
)

from features.secretsanta import SecretSantaRepository, SecretSantaSession
from features.secretsanta.dates import participant_expiry, reminder_due
from features.secretsanta.relay import MAX_MESSAGE_LENGTH, TOO_MANY, RelayLimiter
from src.core import clock
from src.core import logging as logutil
from src.core.logging import log_failure
from src.core.text import EMBED_DESCRIPTION_LIMIT, clip, format_discord_timestamp
from src.discord_ext.look import card, sign
from src.discord_ext.mentions import NO_PINGS
from src.discord_ext.messages import fetch_user_safe
from src.discord_ext.secretsanta import (
    RECEIVED,
    REPLY,
    REPORT,
    SENT,
    WRITE,
    context_guild_id,
    gift_buttons,
    receiver_display_name,
)

from ._common import SANTA, relay_enabled, reminder_days

logger = logutil.init_logger(__name__)

WRITE_MODAL = "secretsanta_write_modal"
REPLY_MODAL = "secretsanta_reply_modal"
MESSAGE_FIELD = "message"

RELAY_OFF = "Les messages anonymes sont désactivés pour cette session."
NOT_IN_DRAW = "Tu ne fais pas partie de ce tirage."
NO_PARCELS = "Cette session n'a pas de suivi de colis."


def _receiver_of(results: list[tuple[int, int]], user_id: int) -> int | None:
    return next((r for g, r in results if g == user_id), None)


def _giver_of(results: list[tuple[int, int]], user_id: int) -> int | None:
    return next((g for g, r in results if r == user_id), None)


def _message_modal(custom_id: str, title: str) -> Modal:
    return Modal(
        ParagraphText(
            label="Ton message",
            custom_id=MESSAGE_FIELD,
            placeholder="Il sera transmis sans ton nom.",
            required=True,
            max_length=MAX_MESSAGE_LENGTH,
        ),
        title=title,
        custom_id=custom_id,
    )


def _report_button(context_id: str, kind: str) -> Button:
    return Button(
        style=ButtonStyle.SECONDARY,
        label="🚩 Signaler",
        custom_id=f"{REPORT}:{context_id}:{kind}",
    )


def relayed_components(context_id: str, *, to_recipient: bool) -> list[ActionRow]:
    """Under a relayed message: answer it, or report it to the organiser."""
    if to_recipient:  # from the Santa: the recipient answers, or reports the Santa
        answer = Button(
            style=ButtonStyle.PRIMARY, label="↩️ Répondre", custom_id=f"{REPLY}:{context_id}"
        )
        return spread_to_rows(answer, _report_button(context_id, "santa"))
    answer = Button(
        style=ButtonStyle.PRIMARY, label="✉️ Répondre", custom_id=f"{WRITE}:{context_id}"
    )
    return spread_to_rows(answer, _report_button(context_id, "recipient"))


def _days_left_text(exchange_day: Any, today: Any) -> str:
    days = (exchange_day - today).days
    if days <= 0:
        return "C'est aujourd'hui"
    if days == 1:
        return "C'est demain"
    return f"Plus que {days} jours"


class ExchangeMixin:
    """The DM buttons of a drawn session, and the parcel reminder task."""

    bot: Client
    repository: SecretSantaRepository
    relay_limiter: RelayLimiter

    async def _draw_of(self, ctx: ComponentContext | ModalContext, context_id: str):
        """``(session, results)`` of a drawn session; answers and gives ``None`` otherwise."""
        session = await self.repository.get_session(context_id)
        results = await self.repository.get_draw_results(context_id) if session else None
        if session is None or not session.is_drawn or not results:
            await ctx.send("Ce tirage n'existe plus.", ephemeral=True)
            return None
        return session, results

    async def _guild_name(self, context_id: str) -> str | None:
        guild_id = context_guild_id(context_id)
        guild = self.bot.get_guild(int(guild_id)) if guild_id else None
        return getattr(guild, "name", None)

    # ── Anonymous messages ───────────────────────────────────────────────

    @component_callback(re.compile(rf"{WRITE}:(.+)"))
    async def on_write_button(self, ctx: ComponentContext) -> None:
        await self._open_message_modal(ctx, to_recipient=True)

    @component_callback(re.compile(rf"{REPLY}:(.+)"))
    async def on_reply_button(self, ctx: ComponentContext) -> None:
        await self._open_message_modal(ctx, to_recipient=False)

    async def _open_message_modal(self, ctx: ComponentContext, *, to_recipient: bool) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        if not relay_enabled(context_id):
            await ctx.send(RELAY_OFF, ephemeral=True)
            return
        draw = await self._draw_of(ctx, context_id)
        if draw is None:
            return
        _, results = draw
        find = _receiver_of if to_recipient else _giver_of
        if find(results, ctx.author.id) is None:
            await ctx.send(NOT_IN_DRAW, ephemeral=True)
            return
        if to_recipient:
            modal = _message_modal(f"{WRITE_MODAL}:{context_id}", "Écrire à ton destinataire")
        else:
            modal = _message_modal(f"{REPLY_MODAL}:{context_id}", "Répondre à ton Père Noël")
        await ctx.send_modal(modal)

    @modal_callback(re.compile(rf"{WRITE_MODAL}:(.+)"))
    async def on_write_modal(self, ctx: ModalContext, **responses: str) -> None:
        await self._relay(ctx, responses, to_recipient=True)

    @modal_callback(re.compile(rf"{REPLY_MODAL}:(.+)"))
    async def on_reply_modal(self, ctx: ModalContext, **responses: str) -> None:
        await self._relay(ctx, responses, to_recipient=False)

    async def _relay(
        self, ctx: ModalContext, responses: dict[str, str], *, to_recipient: bool
    ) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        if not relay_enabled(context_id):
            await ctx.send(RELAY_OFF, ephemeral=True)
            return
        text = (responses.get(MESSAGE_FIELD) or "").strip()
        if not text:
            await ctx.send("Ton message est vide.", ephemeral=True)
            return
        draw = await self._draw_of(ctx, context_id)
        if draw is None:
            return
        _, results = draw
        sender = ctx.author.id
        target_id = (_receiver_of if to_recipient else _giver_of)(results, sender)
        if target_id is None:
            await ctx.send(NOT_IN_DRAW, ephemeral=True)
            return
        key = (context_id, sender, "write" if to_recipient else "reply")
        now = clock.now()
        retry = self.relay_limiter.retry_at(key, now)
        if retry is not None:
            when = format_discord_timestamp(retry, "R")
            await ctx.send(TOO_MANY.format(when=when), ephemeral=True)
            return

        guild_name = await self._guild_name(context_id)
        if to_recipient:
            title = "💌 Message de ton Père Noël secret"
        else:
            _, me = await fetch_user_safe(self.bot, sender)
            name = receiver_display_name(context_guild_id(context_id), sender, me)
            title = "💬 Réponse de ton destinataire"
            text = f"**{name}** te répond :\n\n{text}"
        embed = card(
            "moduleSecretSanta", title=title, description=clip(text, EMBED_DESCRIPTION_LIMIT)
        )
        if guild_name:
            sign(embed, "moduleSecretSanta", guild_name)
        _, target = await fetch_user_safe(self.bot, target_id)
        try:
            await target.send(
                embed=embed,
                components=relayed_components(context_id, to_recipient=to_recipient),
                allowed_mentions=NO_PINGS,
            )
        except Exception as e:
            log_failure(logger, "Secret Santa relay in %s failed: %s", context_id, e)
            who = "Ton destinataire" if to_recipient else "Ton Père Noël secret"
            await ctx.send(
                f"Ton message n'a pas pu être remis : {who} n'accepte pas les messages "
                "privés du bot. Réessaie plus tard.",
                ephemeral=True,
            )
            return
        self.relay_limiter.record(key, now)
        logger.info("Secret Santa message relayed in %s", context_id)
        await ctx.send("💌 Message remis, sans ton nom.", ephemeral=True)

    @component_callback(re.compile(rf"{REPORT}:(.+):(santa|recipient)"))
    async def on_report_button(self, ctx: ComponentContext) -> None:
        context_id, kind = ctx.custom_id.split(":", 1)[1].rsplit(":", 1)
        draw = await self._draw_of(ctx, context_id)
        if draw is None:
            return
        session, results = draw
        reporter = ctx.author.id
        sender = (_giver_of if kind == "santa" else _receiver_of)(results, reporter)
        if sender is None:
            await ctx.send(NOT_IN_DRAW, ephemeral=True)
            return
        key = (context_id, reporter, "report")
        now = clock.now()
        if self.relay_limiter.retry_at(key, now) is not None:
            await ctx.send(
                "Tu as déjà signalé plusieurs messages : patiente un peu.", ephemeral=True
            )
            return
        embeds = getattr(ctx.message, "embeds", None) or []
        content = getattr(embeds[0], "description", None) if embeds else None
        if not await self._notify_organiser(session, reporter, sender, content or "(vide)"):
            await ctx.send(
                "Je n'ai pas pu prévenir l'organisateur de la session : contacte un "
                "administrateur du serveur.",
                ephemeral=True,
            )
            return
        self.relay_limiter.record(key, now)
        logger.info("Secret Santa message reported in %s", context_id)
        await ctx.send(
            "🚩 Message signalé à l'organisateur de la session, qui voit qui l'a envoyé.",
            ephemeral=True,
        )

    async def _notify_organiser(
        self, session: SecretSantaSession, reporter: int, sender: int, content: str
    ) -> bool:
        organiser = session.created_by
        if organiser in (reporter, sender):
            return False
        _, user = await fetch_user_safe(self.bot, organiser)
        if user is None:
            return False
        guild_name = await self._guild_name(session.context_id)
        embed = card(
            "moduleSecretSanta",
            title="🚩 Message signalé — Père Noël Secret",
            description=(
                clip(
                    f"**Signalé par :** <@{reporter}>\n**Envoyé par :** <@{sender}>\n\n{content}",
                    EMBED_DESCRIPTION_LIMIT,
                )
            ),
        )
        if guild_name:
            sign(embed, "moduleSecretSanta", guild_name)
        try:
            await user.send(embed=embed, allowed_mentions=NO_PINGS)
        except Exception as e:
            log_failure(logger, "Could not DM the Secret Santa organiser: %s", e)
            return False
        return True

    # ── Parcel marks ─────────────────────────────────────────────────────

    @component_callback(re.compile(rf"{SENT}:(.+)"))
    async def on_sent_button(self, ctx: ComponentContext) -> None:
        await self._mark_gift(ctx, sent=True)

    @component_callback(re.compile(rf"{RECEIVED}:(.+)"))
    async def on_received_button(self, ctx: ComponentContext) -> None:
        await self._mark_gift(ctx, sent=False)

    async def _mark_gift(self, ctx: ComponentContext, *, sent: bool) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        draw = await self._draw_of(ctx, context_id)
        if draw is None:
            return
        session, results = draw
        if not session.shipping:
            await ctx.send(NO_PARCELS, ephemeral=True)
            return
        me = ctx.author.id
        other = (_receiver_of if sent else _giver_of)(results, me)
        if other is None:
            await ctx.send(NOT_IN_DRAW, ephemeral=True)
            return
        expires = participant_expiry(session.exchange_day, session.created)
        mark = "sent_at" if sent else "received_at"
        if not await self.repository.mark_gift(context_id, me, mark, expires):
            await ctx.send(
                "Ton cadeau est déjà marqué envoyé."
                if sent
                else "Tu as déjà indiqué avoir reçu ton cadeau.",
                ephemeral=True,
            )
            return
        _, user = await fetch_user_safe(self.bot, other)
        guild_id = context_guild_id(context_id)
        if sent:
            embed = card(
                "moduleSecretSanta",
                title="📦 Ton cadeau est en route !",
                description=(
                    "Ton Père Noël secret a envoyé ton cadeau. Quand il arrive, clique sur "
                    "**🎁 Cadeau reçu** pour le prévenir."
                ),
            )
            components = spread_to_rows(
                Button(
                    style=ButtonStyle.SUCCESS,
                    label="🎁 Cadeau reçu",
                    custom_id=f"{RECEIVED}:{context_id}",
                )
            )
        else:
            name = receiver_display_name(guild_id, me, ctx.author)
            embed = card(
                "moduleSecretSanta",
                "success",
                title="🎁 Cadeau bien arrivé !",
                description=f"Ton cadeau pour **{name}** est bien arrivé. Merci, Père Noël ! 🎅",
            )
            components = []
        try:
            await user.send(embed=embed, components=components)
            told = True
        except Exception as e:
            log_failure(logger, "Could not tell the other side of a gift in %s: %s", context_id, e)
            told = False
        if sent:
            reply = "📦 C'est noté !" + (
                " Ton destinataire est prévenu."
                if told
                else " Ton destinataire n'a pas pu être prévenu (MP fermés)."
            )
        else:
            reply = "🎁 C'est noté, merci !" + (
                " Ton Père Noël secret est prévenu." if told else ""
            )
        await ctx.send(reply, ephemeral=True)

    # ── The reminder before the exchange ─────────────────────────────────

    @listen()
    async def on_startup(self) -> None:
        if not self.parcel_reminders.running:
            self.parcel_reminders.start()

    @Task.create(IntervalTrigger(hours=1))
    async def parcel_reminders(self) -> None:
        now = clock.now()
        for guild_id, settings in SANTA.items():
            try:
                await self.remind_parcels(f"guild_{guild_id}", settings, now)
            except Exception as e:
                log_failure(logger, "Secret Santa reminder for guild %s failed: %s", guild_id, e)

    async def remind_parcels(self, context_id: str, settings: dict, now: datetime) -> int:
        """DM every Santa whose parcel isn't marked sent, once per session; how many."""
        session = await self.repository.get_session(context_id)
        day = session.exchange_day if session else None
        if session is None or not session.is_drawn or not session.shipping or day is None:
            return 0
        if session.reminded or not reminder_due(day, reminder_days(settings), now):
            return 0
        if not await self.repository.claim_reminder(context_id):
            return 0
        results = await self.repository.get_draw_results(context_id) or []
        sent, _ = await self.repository.gift_marks(context_id)
        guild_id = context_guild_id(context_id)
        lead = _days_left_text(day, clock.to_local(now).date())
        relay = relay_enabled(context_id)
        count = 0
        for giver_id, receiver_id in results:
            if giver_id in sent:
                continue
            _, giver = await fetch_user_safe(self.bot, giver_id)
            _, receiver = await fetch_user_safe(self.bot, receiver_id)
            name = receiver_display_name(guild_id, receiver_id, receiver)
            embed = card(
                "moduleSecretSanta",
                title="⏰ Père Noël Secret",
                description=(
                    f"{lead} avant l'échange ! Ton cadeau pour **{name}** n'est pas encore "
                    "marqué envoyé.\n\nS'il est parti, clique sur **📦 Cadeau envoyé** "
                    "pour prévenir ton destinataire."
                ),
            )
            try:
                await giver.send(
                    embed=embed,
                    components=gift_buttons(context_id, relay=relay, shipping=True),
                )
                count += 1
            except Exception as e:
                log_failure(logger, "Secret Santa reminder DM to %s failed: %s", giver_id, e)
        logger.info("Secret Santa parcel reminder in %s: %s DM(s)", context_id, count)
        return count


__all__ = ["ExchangeMixin", "relayed_components"]
