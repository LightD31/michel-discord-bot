"""Draw, reveal, and remind subcommands."""

import os
from datetime import datetime

from interactions import (
    Embed,
    IntegrationType,
    OptionType,
    Permissions,
    SlashContext,
    slash_command,
    slash_option,
)

from features.secretsanta import (
    SecretSantaSession,
    generate_assignments_with_subgroups,
    generate_valid_assignments,
)
from src import logutil
from src.config_manager import load_discord2name
from src.helpers import Colors, fetch_user_safe, send_error

from ._common import DATA_DIR, create_join_buttons, get_context_id

logger = logutil.init_logger(os.path.basename(__file__))


class DrawsMixin:
    """Run the draw, reveal results, and let participants remind themselves."""

    async def _save_human_readable_draw(
        self,
        context_id: str,
        assignments: list[tuple[int, int]],
        session: SecretSantaSession,
        context_name: str,
    ) -> None:
        draw_file = (
            DATA_DIR
            / f"draw_{context_id.replace(':', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )

        lines = [
            "=" * 50,
            "🎅 PÈRE NOËL SECRET - RÉSULTATS DU TIRAGE 🎅",
            "=" * 50,
            "",
            f"📅 Date du tirage : {datetime.now().strftime('%d/%m/%Y à %H:%M')}",
            f"📍 Contexte : {context_name}",
            f"👥 Nombre de participants : {len(assignments)}",
        ]

        if session.budget:
            lines.append(f"💰 Budget suggéré : {session.budget}")
        if session.deadline:
            lines.append(f"📆 Date limite : {session.deadline}")

        lines.extend(["", "-" * 50, "ATTRIBUTIONS :", "-" * 50, ""])

        for giver_id, receiver_id in assignments:
            _, giver = await fetch_user_safe(self.bot, giver_id)
            giver_name = (
                f"{giver.display_name} (@{giver.username})" if giver else f"Utilisateur #{giver_id}"
            )

            _, receiver = await fetch_user_safe(self.bot, receiver_id)
            receiver_name = (
                f"{receiver.display_name} (@{receiver.username})"
                if receiver
                else f"Utilisateur #{receiver_id}"
            )

            lines.append(f"  🎁 {giver_name}  →  {receiver_name}")

        lines.extend(
            [
                "",
                "=" * 50,
                "Fichier généré automatiquement par Michel Bot",
                "=" * 50,
            ]
        )

        draw_file.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Human-readable draw results saved to {draw_file}")

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        dm_permission=True,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
        sub_cmd_name="draw",
        sub_cmd_description="Effectue le tirage au sort",
    )
    @slash_option(
        name="allow_subgroups",
        description="Autoriser plusieurs sous-groupes si une boucle unique est impossible",
        required=False,
        opt_type=OptionType.BOOLEAN,
    )
    async def draw(self, ctx: SlashContext, allow_subgroups: bool = False) -> None:
        await ctx.defer(ephemeral=True)

        context_id = get_context_id(ctx)
        session = await self.repository.get_session(context_id)

        if not session:
            await send_error(
                ctx,
                "Il n'y a pas de session de Père Noël Secret en cours.\nCréez-en une avec `/secretsanta create`",
            )
            return

        if session.is_drawn:
            await send_error(ctx, "Le tirage au sort a déjà été effectué pour cette session !")
            return

        if len(session.participants) < 2:
            await send_error(
                ctx,
                f"Il faut au moins 2 participants pour le tirage.\nParticipants actuels : {len(session.participants)}",
            )
            return

        banned_pairs = await self.repository.read_banned_pairs(context_id)

        num_subgroups = 1
        assignments = generate_valid_assignments(session.participants, banned_pairs)

        if not assignments and allow_subgroups:
            result = generate_assignments_with_subgroups(session.participants, banned_pairs)
            if result:
                assignments, num_subgroups = result
                logger.info(f"Draw completed with {num_subgroups} subgroup(s) for {context_id}")

        if not assignments:
            error_msg = "Impossible de générer un tirage valide avec les restrictions actuelles.\n"
            if not allow_subgroups:
                error_msg += "\n💡 **Astuce :** Essayez avec l'option `allow_subgroups: True` pour autoriser plusieurs sous-groupes."
            error_msg += "\nVérifiez les paires interdites avec `/secretsanta listbans`"

            await send_error(ctx, error_msg)
            return

        server = str(ctx.guild.id) if ctx.guild else None
        discord2name_data = load_discord2name(server) if server else {}

        failed_dms = []
        for giver_id, receiver_id in assignments:
            try:
                _, giver = await fetch_user_safe(self.bot, giver_id)
                _, receiver = await fetch_user_safe(self.bot, receiver_id)

                receiver_name = discord2name_data.get(
                    str(receiver_id),
                    receiver.mention if receiver else f"<@{receiver_id}>",
                )

                dm_embed = Embed(
                    title="🎅 Père Noël Secret",
                    description=(
                        f"🎄 Ho, ho, ho ! C'est le Père Noël ! 🎄\n\n"
                        f"Cette année, tu dois offrir un cadeau à **{receiver_name}** !\n"
                        f"À toi de voir s'il/elle a été sage... 😉\n\n"
                        + (f"💰 **Budget suggéré :** {session.budget}\n" if session.budget else "")
                        + (f"📅 **Date limite :** {session.deadline}\n" if session.deadline else "")
                        + "\n*Signé : Le vrai Père Noël* 🎅"
                    ),
                    color=Colors.SECRET_SANTA,
                )

                await giver.send(embed=dm_embed)
            except Exception as e:
                logger.error(f"Failed to send DM to {giver_id}: {e}")
                failed_dms.append(giver_id)

        await self.repository.save_draw_results(context_id, assignments)
        session.is_drawn = True
        await self.repository.save_session(session)

        context_name = ctx.guild.name if ctx.guild else f"DM Group {ctx.channel.id}"
        await self._save_human_readable_draw(context_id, assignments, session, context_name)

        participant_mentions = []
        for uid in session.participants:
            _, user = await fetch_user_safe(self.bot, uid)
            participant_mentions.append(user.mention if user else f"<@{uid}>")

        draw_embed = Embed(
            title="🎅 Tirage effectué ! 🎉",
            description=(
                f"Le tirage au sort a été effectué pour **{len(session.participants)}** participants !\n\n"
                f"**Participants :**\n" + "\n".join(f"• {m}" for m in participant_mentions) + "\n\n"
                "Vérifiez vos messages privés pour découvrir qui vous devez gâter ! 🎁"
            ),
            color=Colors.SECRET_SANTA,
        )

        if ctx.guild:
            if session.message_id:
                try:
                    channel = self.bot.get_channel(session.channel_id)
                    if channel:
                        message = await channel.fetch_message(session.message_id)
                        await message.edit(
                            embed=draw_embed,
                            components=create_join_buttons(context_id, disabled=True),
                        )
                except Exception as e:
                    logger.error(f"Failed to update session message: {e}")
        else:
            try:
                channel = self.bot.get_channel(session.channel_id)
                if channel:
                    await channel.send(embed=draw_embed)
            except Exception as e:
                logger.error(f"Failed to send draw announcement in DM group: {e}")

        response_msg = (
            f"🎉 Le tirage a été effectué pour {len(session.participants)} participants !"
        )
        if num_subgroups > 1:
            response_msg += f"\n\n🔄 **{num_subgroups} sous-groupes** ont été formés (les contraintes empêchaient une boucle unique)."
        if failed_dms:
            failed_mentions = [f"<@{uid}>" for uid in failed_dms]
            response_msg += f"\n\n⚠️ Impossible d'envoyer un DM à : {', '.join(failed_mentions)}"

        await ctx.send(
            embed=Embed(
                title="🎅 Tirage effectué",
                description=response_msg,
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        dm_permission=True,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
        sub_cmd_name="reveal",
        sub_cmd_description="Révèle les attributions (admin uniquement)",
    )
    async def reveal_assignments(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)

        is_admin = ctx.author.has_permission(Permissions.ADMINISTRATOR) if ctx.guild else False
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
            embed=Embed(
                title="🎅 Révélation des attributions",
                description=description,
                color=Colors.SECRET_SANTA,
            ),
            ephemeral=True,
        )

    @slash_command(
        name="secretsanta",
        description="Commandes du Père Noël Secret",
        dm_permission=True,
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
        sub_cmd_name="remind",
        sub_cmd_description="Renvoie votre attribution par DM",
    )
    async def remind_assignment(self, ctx: SlashContext) -> None:
        context_id = get_context_id(ctx)
        results = await self.repository.get_draw_results(context_id)

        if not results:
            await send_error(ctx, "Aucun tirage n'a été effectué pour cette session.")
            return

        user_assignment = None
        for giver_id, receiver_id in results:
            if giver_id == ctx.author.id:
                user_assignment = receiver_id
                break

        if not user_assignment:
            await send_error(ctx, "Vous n'avez pas participé à ce tirage.")
            return

        session = await self.repository.get_session(context_id)
        server = str(ctx.guild.id) if ctx.guild else None
        discord2name_data = load_discord2name(server) if server else {}

        try:
            _, receiver = await fetch_user_safe(self.bot, user_assignment)
            receiver_name = discord2name_data.get(
                str(user_assignment),
                receiver.mention if receiver else f"<@{user_assignment}>",
            )

            dm_embed = Embed(
                title="🎅 Rappel - Père Noël Secret",
                description=(
                    f"🎄 Rappel : Tu dois offrir un cadeau à **{receiver_name}** ! 🎁\n\n"
                    + (
                        f"💰 **Budget suggéré :** {session.budget}\n"
                        if session and session.budget
                        else ""
                    )
                    + (
                        f"📅 **Date limite :** {session.deadline}\n"
                        if session and session.deadline
                        else ""
                    )
                ),
                color=Colors.SECRET_SANTA,
            )

            await ctx.author.send(embed=dm_embed)
            await ctx.send(
                embed=Embed(
                    title="🎅 Rappel envoyé",
                    description="Vérifiez vos messages privés ! 📬",
                    color=Colors.SECRET_SANTA,
                ),
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Failed to send reminder DM: {e}")
            await send_error(
                ctx,
                "Impossible d'envoyer le message privé. Vérifiez que vos DMs sont ouverts.",
            )
