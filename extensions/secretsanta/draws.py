"""Draw, reveal, and remind subcommands."""

from interactions import (
    Client,
    Embed,
    OptionType,
    SlashContext,
    slash_command,
    slash_option,
)

from features.secretsanta import SecretSantaRepository
from src.core import logging as logutil
from src.core.logging import log_failure
from src.discord_ext.look import card
from src.discord_ext.messages import fetch_user_safe, send_error
from src.discord_ext.scopes import guild_scopes
from src.discord_ext.secretsanta import (
    IMPOSSIBLE,
    context_guild_id,
    draw_dm_embed,
    gift_buttons,
    receiver_display_name,
    run_draw,
)

from ._common import SANTA, get_context_id, is_guild_admin, relay_enabled

logger = logutil.init_logger(__name__)


class DrawsMixin:
    """Run the draw, reveal results, and let participants remind themselves."""

    bot: Client
    repository: SecretSantaRepository

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="tirage",
        sub_cmd_description="Effectue le tirage au sort",
    )
    @slash_option(
        name="sous_groupes",
        description="Autoriser plusieurs sous-groupes si une boucle unique est impossible",
        required=False,
        opt_type=OptionType.BOOLEAN,
        argument_name="allow_subgroups",
    )
    async def draw(self, ctx: SlashContext, allow_subgroups: bool = False) -> None:
        await ctx.defer(ephemeral=True)

        context_id = get_context_id(ctx)
        session = await self.repository.get_session(context_id)

        if not session:
            await send_error(
                ctx,
                "Il n'y a pas de session de Père Noël Secret en cours.\n"
                "Crées-en une avec `/secretsanta creer`.",
            )
            return

        # Same rule as /secretsanta annuler: drawing closes the session for
        # everyone and DMs every participant.
        is_creator = ctx.author.id == session.created_by
        is_admin = is_guild_admin(ctx)
        if not is_creator and not is_admin:
            await send_error(
                ctx, "Seul le créateur de la session ou un administrateur peut lancer le tirage."
            )
            return

        outcome = await run_draw(
            self.bot,
            self.repository,
            session,
            allow_subgroups=allow_subgroups,
            relay=relay_enabled(context_id),
            context_name=ctx.guild.name if ctx.guild else f"DM Group {ctx.channel.id}",
            announce_channel=None if ctx.guild else ctx.channel,
        )
        if outcome.error:
            message = outcome.error
            if outcome.error == IMPOSSIBLE:
                if not allow_subgroups:
                    message += (
                        "\n\n💡 **Astuce :** essaie avec l'option `sous_groupes: True` "
                        "pour autoriser plusieurs sous-groupes."
                    )
                message += "\nVérifie les paires interdites avec `/secretsanta paires-exclues`."
            await send_error(ctx, message)
            return

        response_msg = f"🎉 Le tirage a été effectué pour {outcome.count} participants !"
        if outcome.subgroups > 1:
            response_msg += (
                f"\n\n🔄 **{outcome.subgroups} sous-groupes** ont été formés "
                "(les contraintes empêchaient une boucle unique)."
            )
        if outcome.failed_dms:
            failed_mentions = [f"<@{uid}>" for uid in outcome.failed_dms]
            response_msg += (
                f"\n\n⚠️ Impossible d'envoyer un DM à : {', '.join(failed_mentions)}\n"
                "Une fois leurs messages privés ouverts, `/secretsanta relancer` leur renvoie "
                "leur attribution."
            )

        await ctx.send(
            embed=card("moduleSecretSanta", title="🎅 Tirage effectué", description=response_msg),
            ephemeral=True,
        )

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="reveler",
        sub_cmd_description="Révèle les attributions (admin uniquement)",
    )
    async def reveal_assignments(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)

        is_admin = is_guild_admin(ctx)
        if not is_admin and ctx.guild:
            await send_error(ctx, "Seul un administrateur peut révéler les attributions.")
            return

        results = await self.repository.get_draw_results(context_id)

        if not results:
            await send_error(ctx, "Aucun tirage n'a été effectué pour cette session.")
            return

        description = "**Attributions :**\n\n"
        for giver_id, receiver_id in results:
            _, giver = await fetch_user_safe(self.bot, giver_id)
            _, receiver = await fetch_user_safe(self.bot, receiver_id)
            giver_mention = giver.mention if giver else f"<@{giver_id}>"
            receiver_mention = receiver.mention if receiver else f"<@{receiver_id}>"
            description += f"• {giver_mention} → {receiver_mention}\n"

        await ctx.send(
            embed=card(
                "moduleSecretSanta", title="🎅 Révélation des attributions", description=description
            ),
            ephemeral=True,
        )

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        scopes=guild_scopes(SANTA.guild_ids()),
        sub_cmd_name="relancer",
        sub_cmd_description="Renvoie ton attribution, ta liste et tes boutons par MP",
    )
    async def remind_assignment(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)
        results = await self.repository.get_draw_results(context_id)

        if not results:
            await send_error(ctx, "Aucun tirage n'a été effectué pour cette session.")
            return

        receiver_id = next((r for g, r in results if g == ctx.author.id), None)
        if not receiver_id:
            await send_error(ctx, "Tu n'as pas participé à ce tirage.")
            return

        session = await self.repository.get_session(context_id)
        if session is None:
            await send_error(ctx, "Cette session n'existe plus.")
            return
        relay = relay_enabled(context_id)
        try:
            _, receiver = await fetch_user_safe(self.bot, receiver_id)
            receiver_entry = await self.repository.get_participant(context_id, receiver_id)
            embed = draw_dm_embed(
                session,
                receiver_display_name(context_guild_id(context_id), receiver_id, receiver),
                receiver_entry.wishlist if receiver_entry else None,
                relay=relay,
                title="🎅 Rappel - Père Noël Secret",
                footer=ctx.guild.name if ctx.guild else None,
            )
            await ctx.author.send(
                embed=embed,
                components=gift_buttons(context_id, relay=relay, shipping=session.shipping),
            )
            await ctx.send(
                embed=card(
                    "moduleSecretSanta",
                    title="🎅 Rappel envoyé",
                    description="Regarde tes messages privés ! 📬",
                ),
                ephemeral=True,
            )
        except Exception as e:
            log_failure(logger, f"Failed to send reminder DM: {e}")
            await send_error(
                ctx,
                "Impossible de t'envoyer le message privé. Vérifie que tes MP sont ouverts.",
            )
