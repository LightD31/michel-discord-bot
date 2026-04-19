"""Group management commands: create, join, leave."""

import os
from datetime import datetime

from interactions import (
    AutocompleteContext,
    Embed,
    OptionType,
    SlashContext,
    slash_command,
    slash_option,
)

from src.core import logging as logutil
from src.discord_ext.autocomplete import guild_group_autocomplete
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild

from ._common import groups_col

logger = logutil.init_logger(os.path.basename(__file__))


class GroupsMixin:
    """Create, join, and leave Tricount expense groups."""

    @slash_command(
        name="tricount",
        description="Gestion des dépenses partagées",
        sub_cmd_name="groupe",
        sub_cmd_description="Créer un nouveau groupe de dépenses",
    )
    @slash_option(
        name="nom",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        max_length=50,
    )
    @slash_option(
        name="description",
        description="Description du groupe",
        opt_type=OptionType.STRING,
        required=False,
        max_length=200,
    )
    async def tricount_groupe(
        self,
        ctx: SlashContext,
        nom: str,
        description: str | None = None,
    ):
        if not await require_guild(ctx):
            return

        existing_group = await groups_col(ctx.guild.id).find_one({"name": nom})
        if existing_group:
            await ctx.send(
                f"❌ Un groupe avec le nom '{nom}' existe déjà sur ce serveur.", ephemeral=True
            )
            return

        group_data = {
            "name": nom,
            "description": description or "",
            "creator": ctx.author.id,
            "members": [ctx.author.id],
            "created_at": datetime.now(),
            "is_active": True,
        }
        result = await groups_col(ctx.guild.id).insert_one(group_data)
        group_id = result.inserted_id

        embed = Embed(
            title="✅ Groupe créé",
            description=f"Le groupe **{nom}** a été créé avec succès !",
            color=Colors.SUCCESS,
        )
        embed.add_field(name="ID du groupe", value=str(group_id), inline=True)
        embed.add_field(name="Créateur", value=ctx.author.mention, inline=True)
        if description:
            embed.add_field(name="Description", value=description, inline=False)

        logger.info(
            "Groupe Tricount '%s' créé par %s sur le serveur %s",
            nom,
            ctx.author.display_name,
            ctx.guild.name,
        )
        await ctx.send(embed=embed)

    @tricount_groupe.subcommand(
        sub_cmd_name="rejoindre",
        sub_cmd_description="Rejoindre un groupe existant",
    )
    @slash_option(
        name="nom",
        description="Nom du groupe à rejoindre",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def tricount_rejoindre(self, ctx: SlashContext, nom: str):
        if not await require_guild(ctx):
            return

        group = await groups_col(ctx.guild.id).find_one({"name": nom, "is_active": True})
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{nom}'.", ephemeral=True)
            return
        if ctx.author.id in group["members"]:
            await ctx.send("❌ Vous êtes déjà membre de ce groupe.", ephemeral=True)
            return

        await groups_col(ctx.guild.id).update_one(
            {"_id": group["_id"]}, {"$push": {"members": ctx.author.id}}
        )

        embed = Embed(
            title="✅ Groupe rejoint",
            description=f"Vous avez rejoint le groupe **{nom}** !",
            color=Colors.SUCCESS,
        )
        logger.info(
            "Utilisateur %s a rejoint le groupe Tricount '%s' sur le serveur %s",
            ctx.author.display_name,
            nom,
            ctx.guild.name,
        )
        await ctx.send(embed=embed)

    @tricount_rejoindre.autocomplete("nom")
    async def groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, groups_col, member_filter=False)

    @tricount_groupe.subcommand(
        sub_cmd_name="quitter",
        sub_cmd_description="Quitter un groupe",
    )
    @slash_option(
        name="nom",
        description="Nom du groupe à quitter",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def tricount_quitter(self, ctx: SlashContext, nom: str):
        if not await require_guild(ctx):
            return

        group = await groups_col(ctx.guild.id).find_one({"name": nom, "is_active": True})
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{nom}'.", ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send("❌ Vous n'êtes pas membre de ce groupe.", ephemeral=True)
            return

        await groups_col(ctx.guild.id).update_one(
            {"_id": group["_id"]}, {"$pull": {"members": ctx.author.id}}
        )

        embed = Embed(
            title="✅ Groupe quitté",
            description=f"Vous avez quitté le groupe **{nom}**.",
            color=Colors.WARNING,
        )
        logger.info(
            "Utilisateur %s a quitté le groupe Tricount '%s' sur le serveur %s",
            ctx.author.display_name,
            nom,
            ctx.guild.name,
        )
        await ctx.send(embed=embed)

    @tricount_quitter.autocomplete("nom")
    async def groupe_membre_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, groups_col)
