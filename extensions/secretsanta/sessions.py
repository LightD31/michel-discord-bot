"""Session lifecycle subcommands: create / participants / cancel."""

import os

from interactions import (
    Embed,
    IntegrationType,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_option,
)

from features.secretsanta import SecretSantaSession
from src.core import logging as logutil
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import fetch_user_safe, send_error

from ._common import create_join_buttons, get_context_id

logger = logutil.init_logger(os.path.basename(__file__))


class SessionsMixin:
    """Create, list and cancel sessions."""

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        dm_permission=True,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
        sub_cmd_name="create",
        sub_cmd_description="Crée une nouvelle session de Père Noël Secret",
    )
    @slash_option(
        name="budget",
        description="Budget suggéré pour les cadeaux (ex: '20€')",
        required=False,
        opt_type=OptionType.STRING,
    )
    @slash_option(
        name="deadline",
        description="Date limite pour l'échange (ex: '25 décembre')",
        required=False,
        opt_type=OptionType.STRING,
    )
    async def create_session(
        self, ctx: SlashContext, budget: str | None = None, deadline: str | None = None
    ) -> None:
        context_id = get_context_id(ctx)
        existing = await self.repository.get_session(context_id)

        if existing and not existing.is_drawn:
            await send_error(
                ctx,
                "Une session de Père Noël Secret est déjà en cours !\nUtilisez `/secretsanta cancel` pour l'annuler d'abord.",
            )
            return

        session = SecretSantaSession(
            context_id=context_id,
            channel_id=ctx.channel.id,
            created_by=ctx.author.id,
            budget=budget,
            deadline=deadline,
        )

        description = (
            "🎄 **Une session de Père Noël Secret a été créée !** 🎄\n\n"
            "Cliquez sur **Participer** pour rejoindre le tirage au sort.\n"
            "Vous pouvez vous retirer à tout moment avant le tirage.\n\n"
        )

        if budget:
            description += f"💰 **Budget suggéré :** {budget}\n"
        if deadline:
            description += f"📅 **Date limite :** {deadline}\n"

        if ctx.guild:
            description += "\n**Participants (0) :**\n*Aucun participant pour le moment*"
        else:
            description += "\nUtilisez `/secretsanta participants` pour voir la liste des inscrits."

        embed = Embed(
            title="🎅 Père Noël Secret",
            description=description,
            color=Colors.SECRET_SANTA_SUCCESS,
        )

        msg = await ctx.send(embed=embed, components=create_join_buttons(context_id))

        session.message_id = msg.id
        await self.repository.save_session(session)

        logger.info(f"Secret Santa session created by {ctx.author.id} in {context_id}")

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        dm_permission=True,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
        sub_cmd_name="participants",
        sub_cmd_description="Affiche la liste des participants",
    )
    async def list_participants(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)
        session = await self.repository.get_session(context_id)

        if not session:
            await send_error(ctx, "Il n'y a pas de session de Père Noël Secret en cours.")
            return

        if not session.participants:
            description = "*Aucun participant pour le moment*"
        else:
            mentions = []
            for user_id in session.participants:
                _, user = await fetch_user_safe(self.bot, user_id)
                mentions.append(user.mention if user else f"<@{user_id}>")
            description = "\n".join(f"• {m}" for m in mentions)

        status = "✅ Tirage effectué" if session.is_drawn else "⏳ En attente du tirage"

        embed = Embed(
            title=f"🎅 Participants ({len(session.participants)})",
            description=f"**Statut :** {status}\n\n{description}",
            color=Colors.SECRET_SANTA,
        )

        if session.budget:
            embed.add_field(name="💰 Budget", value=session.budget, inline=True)
        if session.deadline:
            embed.add_field(name="📅 Date limite", value=session.deadline, inline=True)

        await ctx.send(embed=embed, ephemeral=True)

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        dm_permission=True,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
        sub_cmd_name="cancel",
        sub_cmd_description="Annule la session en cours",
    )
    async def cancel_session(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)
        session = await self.repository.get_session(context_id)

        if not session:
            await send_error(ctx, "Il n'y a pas de session à annuler.")
            return

        is_creator = ctx.author.id == session.created_by
        is_admin = ctx.author.has_permission(Permissions.ADMINISTRATOR) if ctx.guild else False

        if not is_creator and not is_admin:
            await send_error(
                ctx, "Seul le créateur de la session ou un administrateur peut l'annuler."
            )
            return

        if session.message_id and ctx.guild:
            try:
                channel = self.bot.get_channel(session.channel_id)
                if channel:
                    message = await channel.fetch_message(session.message_id)
                    embed = Embed(
                        title="🎅 Session annulée",
                        description="Cette session de Père Noël Secret a été annulée.",
                        color=Colors.SECRET_SANTA_ACCENT,
                    )
                    await message.edit(embed=embed, components=[])
            except Exception as e:
                logger.error(f"Failed to update cancelled session message: {e}")

        await self.repository.delete_session(context_id)

        await ctx.send(
            embed=Embed(
                title="🎅 Session annulée",
                description="La session de Père Noël Secret a été annulée avec succès.",
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )
