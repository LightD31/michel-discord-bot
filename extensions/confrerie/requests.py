"""`/demande` slash command: user requests for site updates, DMed to the owners."""

import os
from datetime import datetime

from interactions import (
    Embed,
    Modal,
    ModalContext,
    ParagraphText,
    ShortText,
    SlashContext,
    modal_callback,
    slash_command,
)

from src.core import logging as logutil
from src.discord_ext.embeds import Colors, format_discord_timestamp
from src.discord_ext.messages import fetch_user_safe, send_error

from ._common import ConfrerieError, config, enabled_servers, module_config

logger = logutil.init_logger(os.path.basename(__file__))


class RequestsMixin:
    """Accept site-update requests via modal and DM them to the confrérie owners."""

    @slash_command(
        name="demande",
        description="Demander à actualiser le site de la confrérie",
        scopes=[int(s) for s in enabled_servers],
    )
    async def demande(self, ctx: SlashContext):
        modal = Modal(
            ShortText(
                label="Titre",
                custom_id="title",
                placeholder="Titre court de votre demande",
                max_length=100,
            ),
            ParagraphText(
                label="Détails",
                custom_id="details",
                placeholder="Décrivez en détail votre demande d'actualisation",
                max_length=1000,
            ),
            title="Demande d'actualisation",
            custom_id="demande",
        )
        await ctx.send_modal(modal)

    @modal_callback("demande")
    async def demande_callback(self, ctx: ModalContext, title: str, details: str):
        try:
            if not title.strip() or not details.strip():
                await send_error(ctx, "Le titre et les détails sont obligatoires.")
                return

            embed = await self._create_request_embed(ctx, title.strip(), details.strip())
            await self._send_request_to_owners(embed)

            await ctx.send(
                "✅ Demande envoyée avec succès ! Vous recevrez une réponse prochainement.",
                ephemeral=True,
            )
            logger.info(f"Demande d'actualisation envoyée par {ctx.author}: {title}")
        except Exception as e:
            await send_error(ctx, "Une erreur est survenue lors de l'envoi de votre demande.")
            logger.error(f"Erreur lors de l'envoi de la demande: {e}")

    async def _create_request_embed(self, ctx: ModalContext, title: str, details: str) -> Embed:
        embed = Embed(
            title="📝 Nouvelle demande d'actualisation",
            color=Colors.CONFRERIE,
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="👤 Auteur",
            value=f"{ctx.author.mention} ({ctx.author.username})",
            inline=False,
        )
        embed.add_field(name="📋 Titre", value=title, inline=False)
        embed.add_field(name="📝 Détails", value=details, inline=False)
        embed.add_field(
            name="🌐 Serveur",
            value=ctx.guild.name if ctx.guild else "Inconnu",
            inline=True,
        )
        embed.add_field(
            name="📅 Date",
            value=format_discord_timestamp(datetime.now(), "F"),
            inline=True,
        )
        return embed

    async def _send_request_to_owners(self, embed: Embed):
        """DM the confrérie owner and the bot owner; raise if nobody could be reached."""
        owners_sent = 0

        try:
            owner_id = module_config.get("confrerieOwnerId")
            if owner_id:
                _, user = await fetch_user_safe(self.bot, owner_id)
                if user:
                    await user.send(embed=embed)
                    owners_sent += 1
        except Exception as e:
            logger.warning(f"Impossible d'envoyer à l'owner confrérie: {e}")

        try:
            general_owner_id = config["discord"].get("ownerId")
            if general_owner_id and general_owner_id != module_config.get("confrerieOwnerId"):
                _, user2 = await fetch_user_safe(self.bot, general_owner_id)
                if user2:
                    await user2.send(embed=embed)
                    owners_sent += 1
        except Exception as e:
            logger.warning(f"Impossible d'envoyer à l'owner général: {e}")

        if owners_sent == 0:
            raise ConfrerieError("Aucun propriétaire n'a pu être contacté")
