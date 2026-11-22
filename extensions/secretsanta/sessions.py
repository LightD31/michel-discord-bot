"""Session lifecycle subcommands: create / modifier / participants / cancel."""

from interactions import (
    Client,
    Embed,
    OptionType,
    SlashContext,
    slash_command,
    slash_option,
)

from features.secretsanta import SecretSantaRepository, SecretSantaSession
from features.secretsanta.dates import parse_exchange_date, participant_expiry
from features.secretsanta.relay import RelayLimiter
from src.core import clock
from src.core import logging as logutil
from src.core.logging import log_failure
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.look import card
from src.discord_ext.messages import edit_message_if_changed, fetch_user_safe, send_error
from src.discord_ext.scopes import guild_scopes
from src.discord_ext.secretsanta import (
    announcement_embed,
    context_guild_id,
    join_buttons,
    participant_mentions,
    refresh_announcement,
    session_details,
)

from ._common import SANTA, get_context_id, is_guild_admin, may_organise

logger = logutil.init_logger(__name__)

BUDGET_MAX = 100
DATE_MAX = 40


class SessionsMixin:
    """Create, change, list and cancel sessions."""

    bot: Client
    repository: SecretSantaRepository
    relay_limiter: RelayLimiter

    async def _participant_mentions(self, session: SecretSantaSession) -> list[str]:
        return await participant_mentions(self.bot, session)

    async def _refresh_announcement(self, session: SecretSantaSession) -> None:
        """Show the session's current participants and details on its announcement."""
        await refresh_announcement(self.bot, session)

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="creer",
        sub_cmd_description="Crée une nouvelle session de Père Noël Secret",
    )
    @slash_option(
        name="budget",
        description="Budget suggéré pour les cadeaux (ex : 20 €)",
        required=False,
        opt_type=OptionType.STRING,
        max_length=BUDGET_MAX,
    )
    @slash_option(
        name="date",
        description="Date de l'échange (ex : 24/12 ou 24 décembre)",
        required=False,
        opt_type=OptionType.STRING,
        max_length=DATE_MAX,
        argument_name="exchange",
    )
    @slash_option(
        name="colis",
        description="Cadeaux envoyés par la poste : suivi « envoyé / reçu » et rappel",
        required=False,
        opt_type=OptionType.BOOLEAN,
        argument_name="shipping",
    )
    async def create_session(
        self,
        ctx: SlashContext,
        budget: str | None = None,
        exchange: str | None = None,
        shipping: bool = False,
    ) -> None:
        context_id = get_context_id(ctx)
        existing = await self.repository.get_session(context_id)

        if existing and not existing.is_drawn:
            await send_error(
                ctx,
                "Une session de Père Noël Secret est déjà en cours !\n"
                "Annule-la d'abord avec `/secretsanta annuler`.",
            )
            return
        if existing and not may_organise(ctx, existing):
            # Re-creating drops the drawn session (and so /secretsanta reveler).
            await send_error(
                ctx,
                "Seul le créateur de la dernière session ou un administrateur peut en créer "
                "une nouvelle.",
            )
            return
        try:
            day = parse_exchange_date(exchange, clock.local_now().date()) if exchange else None
        except ValueError as e:
            await send_error(ctx, str(e))
            return

        session = SecretSantaSession(
            context_id=context_id,
            channel_id=ctx.channel.id,
            created_by=ctx.author.id,
            budget=budget,
            exchange_date=day.isoformat() if day else None,
            shipping=bool(shipping),
        )
        embed = announcement_embed(session, [] if ctx.guild else None)
        msg = await ctx.send(embed=embed, components=join_buttons(context_id))

        session.message_id = msg.id
        await self.repository.create_session(session)
        self.relay_limiter.forget(context_id)
        logger.info(f"Secret Santa session created by {ctx.author.id} in {context_id}")

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="modifier",
        sub_cmd_description="Change le budget, la date ou l'option colis de la session",
    )
    @slash_option(
        name="budget",
        description="Nouveau budget suggéré (ex : 20 €)",
        required=False,
        opt_type=OptionType.STRING,
        max_length=BUDGET_MAX,
    )
    @slash_option(
        name="date",
        description="Nouvelle date de l'échange (ex : 24/12 ou 24 décembre)",
        required=False,
        opt_type=OptionType.STRING,
        max_length=DATE_MAX,
        argument_name="exchange",
    )
    @slash_option(
        name="colis",
        description="Cadeaux envoyés par la poste : suivi « envoyé / reçu » et rappel",
        required=False,
        opt_type=OptionType.BOOLEAN,
        argument_name="shipping",
    )
    async def modify_session(
        self,
        ctx: SlashContext,
        budget: str | None = None,
        exchange: str | None = None,
        shipping: bool | None = None,
    ) -> None:
        context_id = get_context_id(ctx)
        session = await self.repository.get_session(context_id)
        if not session:
            await send_error(ctx, "Il n'y a pas de session de Père Noël Secret en cours.")
            return
        if not may_organise(ctx, session):
            await send_error(
                ctx, "Seul le créateur de la session ou un administrateur peut la modifier."
            )
            return
        changes: dict = {}
        if budget is not None:
            changes["budget"] = budget
        if exchange is not None:
            try:
                changes["exchange_date"] = parse_exchange_date(
                    exchange, clock.local_now().date()
                ).isoformat()
            except ValueError as e:
                await send_error(ctx, str(e))
                return
        if shipping is not None:
            changes["shipping"] = shipping
        if not changes:
            await send_error(ctx, "Indique au moins un changement : budget, date ou colis.")
            return

        for name, value in changes.items():
            setattr(session, name, value)
        expires = participant_expiry(session.exchange_day, session.created)
        updated = await self.repository.update_settings(context_id, changes, expires)
        if updated is None:
            await send_error(ctx, "Cette session n'existe plus.")
            return
        await self._refresh_announcement(updated)

        text = "La session est à jour.\n\n" + (session_details(updated) or "")
        if updated.is_drawn:
            text += (
                "\n\nLe tirage a déjà eu lieu : chacun retrouve son attribution à jour, "
                "avec ses boutons, grâce à `/secretsanta relancer`."
            )
        await ctx.send(
            embed=card("moduleSecretSanta", title="🎅 Session modifiée", description=text),
            ephemeral=True,
        )
        logger.info("Secret Santa session %s changed by %s: %s", context_id, ctx.author.id, changes)

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="participants",
        sub_cmd_description="Affiche la liste des participants",
    )
    async def list_participants(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)
        session = await self.repository.get_session(context_id)

        if not session:
            await send_error(ctx, "Il n'y a pas de session de Père Noël Secret en cours.")
            return

        mentions = await self._participant_mentions(session)
        listing = "\n".join(f"• {m}" for m in mentions) or "*Aucun participant pour le moment*"
        status = "✅ Tirage effectué" if session.is_drawn else "⏳ En attente du tirage"

        embed = card(
            "moduleSecretSanta",
            title=f"🎅 Participants ({len(session.participants)})",
            description=f"**Statut :** {status}\n\n{listing}",
        )
        details = session_details(session)
        if details:
            embed.add_field(name="Session", value=details, inline=False)
        total = len(session.participants)
        if not session.is_drawn:
            lists = await self.repository.wishlists(context_id)
            filled = len(set(lists) & set(session.participants))
            embed.add_field(name="📝 Listes d'envies", value=f"{filled}/{total}", inline=True)
        elif session.shipping:
            # Counts only: who sent or received would hint at who gives to whom.
            sent, received = await self.repository.gift_marks(context_id)
            embed.add_field(
                name="📦 Colis",
                value=f"Envoyés : {len(sent)}/{total} · Reçus : {len(received)}/{total}",
                inline=False,
            )

        await ctx.send(embed=embed, ephemeral=True)

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="annuler",
        sub_cmd_description="Annule la session en cours",
    )
    async def cancel_session(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)
        session = await self.repository.get_session(context_id)

        if not session:
            await send_error(ctx, "Il n'y a pas de session à annuler.")
            return

        is_creator = ctx.author.id == session.created_by
        is_admin = is_guild_admin(ctx)

        if not is_creator and not is_admin:
            await send_error(
                ctx, "Seul le créateur de la session ou un administrateur peut l'annuler."
            )
            return

        if session.message_id and ctx.guild:
            try:
                channel = await fetch_guild_channel(self.bot, ctx.guild.id, session.channel_id)
                if channel:
                    message = await channel.fetch_message(session.message_id)
                    embed = card(
                        "moduleSecretSanta",
                        title="🎅 Session annulée",
                        description="Cette session de Père Noël Secret a été annulée.",
                    )
                    await edit_message_if_changed(
                        message, embed=embed, components=[], logger=logger
                    )
            except Exception as e:
                log_failure(logger, f"Failed to update cancelled session message: {e}")

        await self.repository.delete_session(context_id)
        self.relay_limiter.forget(context_id)

        await ctx.send(
            embed=card(
                "moduleSecretSanta",
                title="🎅 Session annulée",
                description="La session de Père Noël Secret a été annulée avec succès.",
            ),
            ephemeral=True,
        )
