"""The announcement's buttons: Participer (with a wish list), Ma liste, Se retirer."""

import contextlib
import re
from typing import TYPE_CHECKING

from interactions import (
    Client,
    ComponentContext,
    Modal,
    ModalContext,
    ParagraphText,
    component_callback,
    modal_callback,
)

from features.secretsanta import SecretSantaRepository, SecretSantaSession
from features.secretsanta.dates import participant_expiry
from src.core import logging as logutil
from src.core.logging import log_failure
from src.discord_ext.secretsanta import JOIN, LEAVE, WISHLIST, WISHLIST_LIMIT

logger = logutil.init_logger(__name__)

JOIN_MODAL = "secretsanta_join_modal"
WISHLIST_MODAL = "secretsanta_wishlist_modal"
WISHLIST_FIELD = "envies"

GONE = "Cette session n'existe plus."
DRAWN = "Le tirage a déjà été effectué !"
ALREADY_IN = "Tu participes déjà ! 🎅 Tu peux modifier ta liste avec **📝 Ma liste**."
NOT_IN = "Tu ne participes pas à cette session : clique sur **Participer** d'abord."
OPEN_YOUR_DMS = (
    "Pense à accepter les messages privés de ce serveur : c'est là que tu recevras "
    "le nom de ton destinataire."
)


def wishlist_modal(custom_id: str, title: str, current: str | None = None) -> Modal:
    return Modal(
        ParagraphText(
            label="Ta liste d'envies (facultatif)",
            custom_id=WISHLIST_FIELD,
            placeholder="Idées, tailles, goûts, ce que tu as déjà, ce que tu n'aimes pas…",
            value=current or None,
            required=False,
            max_length=WISHLIST_LIMIT,
        ),
        title=title,
        custom_id=custom_id,
    )


def _wishlist_text(responses: dict[str, str]) -> str | None:
    return (responses.get(WISHLIST_FIELD) or "").strip() or None


class ButtonsMixin:
    """Handle the ``Participer`` / ``📝 Ma liste`` / ``Se retirer`` buttons."""

    bot: Client
    repository: SecretSantaRepository

    if TYPE_CHECKING:

        async def _refresh_announcement(self, session: SecretSantaSession) -> None: ...

    async def _open_session(
        self, ctx: ComponentContext | ModalContext, context_id: str
    ) -> SecretSantaSession | None:
        """The session while it takes sign-ups; answers and gives ``None`` otherwise."""
        session = await self.repository.get_session(context_id)
        if not session:
            await self._send_response(ctx, session, GONE)
            return None
        if session.is_drawn:
            await self._send_response(ctx, session, DRAWN)
            return None
        return session

    @component_callback(re.compile(rf"{JOIN}:(.+)"))
    async def on_join_button(self, ctx: ComponentContext) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        session = await self._open_session(ctx, context_id)
        if session is None:
            return
        if ctx.author.id in session.participants:
            await self._send_response(ctx, session, ALREADY_IN)
            return
        await ctx.send_modal(
            wishlist_modal(f"{JOIN_MODAL}:{context_id}", "Participer au Père Noël Secret")
        )

    @modal_callback(re.compile(rf"{JOIN_MODAL}:(.+)"))
    async def on_join_modal(self, ctx: ModalContext, **responses: str) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        # One conditional update: a concurrent join is kept, and a form sent
        # after the draw changes nothing.
        session = await self.repository.add_participant(context_id, ctx.author.id)
        if session is None:
            current = await self.repository.get_session(context_id)
            if current is None:
                message = GONE
            elif current.is_drawn:
                message = DRAWN
            else:
                message = ALREADY_IN
            await self._send_response(ctx, current, message)
            return
        wishlist = _wishlist_text(responses)
        if wishlist:
            await self.repository.set_wishlist(
                context_id,
                ctx.author.id,
                wishlist,
                participant_expiry(session.exchange_day, session.created),
            )
        logger.info(
            "User %s joined Secret Santa in %s (total: %s)",
            ctx.author.id,
            context_id,
            len(session.participants),
        )
        await self._refresh_announcement(session)
        listed = " avec ta liste d'envies" if wishlist else ""
        await self._send_response(
            ctx, session, f"Tu es inscrit·e au Père Noël Secret{listed} ! 🎁\n{OPEN_YOUR_DMS}"
        )

    @component_callback(re.compile(rf"{WISHLIST}:(.+)"))
    async def on_wishlist_button(self, ctx: ComponentContext) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        session = await self._open_session(ctx, context_id)
        if session is None:
            return
        if ctx.author.id not in session.participants:
            await self._send_response(ctx, session, NOT_IN)
            return
        mine = await self.repository.get_participant(context_id, ctx.author.id)
        await ctx.send_modal(
            wishlist_modal(
                f"{WISHLIST_MODAL}:{context_id}",
                "Ma liste d'envies",
                mine.wishlist if mine else None,
            )
        )

    @modal_callback(re.compile(rf"{WISHLIST_MODAL}:(.+)"))
    async def on_wishlist_modal(self, ctx: ModalContext, **responses: str) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        session = await self._open_session(ctx, context_id)
        if session is None:
            return
        if ctx.author.id not in session.participants:
            await self._send_response(ctx, session, NOT_IN)
            return
        wishlist = _wishlist_text(responses)
        await self.repository.set_wishlist(
            context_id,
            ctx.author.id,
            wishlist,
            participant_expiry(session.exchange_day, session.created),
        )
        await self._send_response(
            ctx,
            session,
            "📝 Ta liste d'envies est enregistrée : ton Père Noël la recevra au tirage."
            if wishlist
            else "📝 Ta liste d'envies est vide.",
        )

    @component_callback(re.compile(rf"{LEAVE}:(.+)"))
    async def on_leave_button(self, ctx: ComponentContext) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        session = await self.repository.get_session(context_id)

        if not session:
            await self._send_response(ctx, session, GONE)
            return

        if session.is_drawn:
            await self._send_response(
                ctx, session, "Le tirage a déjà été effectué, tu ne peux plus te retirer."
            )
            return

        if ctx.author.id not in session.participants:
            await self._send_response(ctx, session, "Tu ne participes pas à cette session.")
            return

        updated = await self.repository.remove_participant(context_id, ctx.author.id)
        if updated is None:
            current = await self.repository.get_session(context_id)
            if current is None:
                message = GONE
            elif current.is_drawn:
                message = "Le tirage a déjà été effectué, tu ne peux plus te retirer."
            else:
                message = "Tu ne participes pas à cette session."
            await self._send_response(ctx, current, message)
            return
        await self.repository.clear_wishlist(context_id, ctx.author.id)
        logger.info(
            "User %s left Secret Santa in %s (total: %s)",
            ctx.author.id,
            context_id,
            len(updated.participants),
        )
        await self._refresh_announcement(updated)
        await self._send_response(ctx, updated, "Tu as été retiré·e du Père Noël Secret.")

    async def _send_response(
        self,
        ctx: ComponentContext | ModalContext,
        session: SecretSantaSession | None,
        message: str,
    ) -> None:
        """Ephemeral in guilds; DM fallback in group DMs where ephemerals are creator-only."""
        if ctx.guild:
            await ctx.send(message, ephemeral=True)
            return

        if session and ctx.author.id == session.created_by:
            await ctx.send(message, ephemeral=True)
        else:
            try:
                await ctx.author.send(f"🎅 **Père Noël Secret**\n{message}")
                await ctx.defer(edit_origin=True)
            except Exception as e:
                log_failure(logger, f"Failed to send DM to {ctx.author.id}: {e}")
                with contextlib.suppress(Exception):
                    await ctx.send(message, ephemeral=True)
