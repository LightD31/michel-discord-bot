"""Join / leave button component callbacks."""

import contextlib
import os
import re

from interactions import ComponentContext, Embed, component_callback

from features.secretsanta import SecretSantaSession
from src import logutil
from src.helpers import Colors, fetch_user_safe

from ._common import create_join_buttons

logger = logutil.init_logger(os.path.basename(__file__))


class ButtonsMixin:
    """Handle the ``Participer`` / ``Se retirer`` buttons on the session embed."""

    @component_callback(re.compile(r"secretsanta_join:(.+)"))
    async def on_join_button(self, ctx: ComponentContext) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        logger.debug(f"Join button clicked by {ctx.author.id} for session {context_id}")
        session = await self.repository.get_session(context_id)

        if not session:
            logger.warning(f"Session not found for {context_id}")
            await self._send_response(ctx, session, "Cette session n'existe plus.")
            return

        if session.is_drawn:
            await self._send_response(ctx, session, "Le tirage a déjà été effectué !")
            return

        if ctx.author.id in session.participants:
            await self._send_response(ctx, session, "Vous participez déjà ! 🎅")
            return

        session.participants.append(ctx.author.id)
        await self.repository.save_session(session)
        logger.info(
            f"User {ctx.author.id} ({ctx.author.username}) joined Secret Santa in {context_id} (total: {len(session.participants)})"
        )

        await self._update_session_message(ctx, session)
        await self._send_response(ctx, session, "Vous êtes inscrit au Père Noël Secret ! 🎁")

    @component_callback(re.compile(r"secretsanta_leave:(.+)"))
    async def on_leave_button(self, ctx: ComponentContext) -> None:
        context_id = ctx.custom_id.split(":", 1)[1]
        logger.debug(f"Leave button clicked by {ctx.author.id} for session {context_id}")
        session = await self.repository.get_session(context_id)

        if not session:
            await self._send_response(ctx, session, "Cette session n'existe plus.")
            return

        if session.is_drawn:
            await self._send_response(
                ctx, session, "Le tirage a déjà été effectué, vous ne pouvez plus vous retirer."
            )
            return

        if ctx.author.id not in session.participants:
            await self._send_response(ctx, session, "Vous ne participez pas à cette session.")
            return

        session.participants.remove(ctx.author.id)
        await self.repository.save_session(session)
        logger.info(
            f"User {ctx.author.id} ({ctx.author.username}) left Secret Santa in {context_id} (total: {len(session.participants)})"
        )

        await self._update_session_message(ctx, session)
        await self._send_response(ctx, session, "Vous avez été retiré du Père Noël Secret.")

    async def _send_response(
        self, ctx: ComponentContext, session: SecretSantaSession | None, message: str
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
                logger.error(f"Failed to send DM to {ctx.author.id}: {e}")
                with contextlib.suppress(Exception):
                    await ctx.send(message, ephemeral=True)

    async def _update_session_message(
        self, ctx: ComponentContext, session: SecretSantaSession
    ) -> None:
        """Refresh the session embed with the current participant list (guild only)."""
        if not ctx.guild:
            return

        try:
            if not session.participants:
                participant_text = "*Aucun participant pour le moment*"
            else:
                mentions = []
                for user_id in session.participants:
                    _, user = await fetch_user_safe(self.bot, user_id)
                    mentions.append(user.mention if user else f"<@{user_id}>")
                participant_text = "\n".join(f"• {m}" for m in mentions)

            description = (
                "🎄 **Une session de Père Noël Secret est en cours !** 🎄\n\n"
                "Cliquez sur **Participer** pour rejoindre le tirage au sort.\n"
                "Vous pouvez vous retirer à tout moment avant le tirage.\n\n"
            )

            if session.budget:
                description += f"💰 **Budget suggéré :** {session.budget}\n"
            if session.deadline:
                description += f"📅 **Date limite :** {session.deadline}\n"

            description += f"\n**Participants ({len(session.participants)}) :**\n{participant_text}"

            embed = Embed(
                title="🎅 Père Noël Secret",
                description=description,
                color=Colors.SECRET_SANTA_SUCCESS,
            )

            await ctx.message.edit(embed=embed, components=create_join_buttons(session.context_id))
        except Exception as e:
            logger.error(f"Failed to update session message: {e}")
