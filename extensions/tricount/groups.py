"""Group management commands: create, join, leave."""

import os
from datetime import datetime

from interactions import (
    AutocompleteContext,
    Embed,
    Member,
    OptionType,
    SlashContext,
    User,
    slash_command,
    slash_option,
)

from features.tricount import (
    TRANSFER,
    TricountRepository,
    compute_balances,
    format_amount,
    to_cents,
)
from src.core import clock
from src.core import logging as logutil
from src.discord_ext.autocomplete import guild_group_autocomplete
from src.discord_ext.look import card, oops
from src.discord_ext.messages import guild_of
from src.discord_ext.scopes import guild_scopes

from ._common import MAX_AMOUNT, TRICOUNT, guild_currency

logger = logutil.init_logger(__name__)


class GroupsMixin:
    """Create, join, and leave Tricount expense groups."""

    @slash_command(
        name="tricount",
        description="Gestion des dépenses partagées",
        sub_cmd_name="groupe",
        sub_cmd_description="Créer un nouveau groupe de dépenses",
        scopes=guild_scopes(TRICOUNT.guild_ids()),
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
        repo = TricountRepository(guild_of(ctx).id)
        existing_group = await repo.find_group_by_name(nom)
        if existing_group:
            await ctx.send(
                oops(f"Un groupe avec le nom '{nom}' existe déjà sur ce serveur."), ephemeral=True
            )
            return

        group_data = {
            "name": nom,
            "description": description or "",
            "creator": ctx.author.id,
            "members": [ctx.author.id],
            "created_at": clock.now(),
            "is_active": True,
        }
        group_id = await repo.create_group(group_data)

        embed = card(
            "moduleTricount",
            "success",
            title="✅ Groupe créé",
            description=f"Le groupe **{nom}** a été créé avec succès !",
        )
        embed.add_field(name="ID du groupe", value=str(group_id), inline=True)
        embed.add_field(name="Créateur", value=ctx.author.mention, inline=True)
        if description:
            embed.add_field(name="Description", value=description, inline=False)

        logger.info(
            "Groupe Tricount '%s' créé par %s sur le serveur %s",
            nom,
            ctx.author.display_name,
            guild_of(ctx).name,
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
        repo = TricountRepository(guild_of(ctx).id)
        group = await repo.find_active_group(nom)
        if not group:
            await ctx.send(oops(f"Aucun groupe actif trouvé avec le nom '{nom}'."), ephemeral=True)
            return
        if ctx.author.id in group["members"]:
            await ctx.send(oops("Tu es déjà membre de ce groupe."), ephemeral=True)
            return

        await repo.add_group_member(group["_id"], ctx.author.id)

        embed = card(
            "moduleTricount",
            "success",
            title="✅ Groupe rejoint",
            description=f"Tu as rejoint le groupe **{nom}** !",
        )
        logger.info(
            "Utilisateur %s a rejoint le groupe Tricount '%s' sur le serveur %s",
            ctx.author.display_name,
            nom,
            guild_of(ctx).name,
        )
        await ctx.send(embed=embed)

    @tricount_rejoindre.autocomplete("nom")
    async def groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(
            ctx, TricountRepository.groups_collection, member_filter=False
        )

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
        repo = TricountRepository(guild_of(ctx).id)
        group = await repo.find_active_group(nom)
        if not group:
            await ctx.send(oops(f"Aucun groupe actif trouvé avec le nom '{nom}'."), ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send(oops("Tu n'es pas membre de ce groupe."), ephemeral=True)
            return

        await repo.remove_group_member(group["_id"], ctx.author.id)
        stopped = await repo.stop_member_recurring(group["_id"], ctx.author.id)

        description = f"Tu as quitté le groupe **{nom}**."
        if stopped:
            description += f"\n\n{stopped} dépense(s) récurrente(s) arrêtée(s)."
        balance = compute_balances(
            await repo.list_group_expenses(group["_id"]), group["members"]
        ).get(ctx.author.id, 0)
        if balance:
            # The shares already split stay theirs: departed members remain in
            # /tricount bilan until a reimbursement brings them back to zero.
            currency = guild_currency(guild_of(ctx).id)
            description += (
                f"\n\nTon solde n'est pas réglé "
                f"({format_amount(balance, currency, signed=True)}) : tu restes dans le bilan "
                "jusqu'à ce qu'un remboursement soit enregistré avec `/tricount rembourser`."
            )
        embed = card("moduleTricount", "neutral", title="✅ Groupe quitté", description=description)
        logger.info(
            "Utilisateur %s a quitté le groupe Tricount '%s' sur le serveur %s",
            ctx.author.display_name,
            nom,
            guild_of(ctx).name,
        )
        await ctx.send(embed=embed)

    @tricount_quitter.autocomplete("nom")
    async def groupe_membre_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, TricountRepository.groups_collection)

    @tricount_groupe.subcommand(
        sub_cmd_name="rembourser",
        sub_cmd_description="Enregistrer un remboursement entre deux personnes d'un groupe",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="destinataire",
        description="Qui a reçu l'argent",
        opt_type=OptionType.USER,
        required=True,
    )
    @slash_option(
        name="montant",
        description="Montant remboursé",
        opt_type=OptionType.NUMBER,
        required=True,
        min_value=0.01,
        max_value=MAX_AMOUNT,
    )
    @slash_option(
        name="payeur",
        description="Qui a payé (par défaut : toi)",
        opt_type=OptionType.USER,
        required=False,
    )
    async def tricount_rembourser(
        self,
        ctx: SlashContext,
        groupe: str,
        destinataire: User | Member,
        montant: float,
        payeur: User | Member | None = None,
    ):
        payeur = payeur or ctx.author
        if montant <= 0:
            await ctx.send(oops("Le montant doit être positif."), ephemeral=True)
            return
        if payeur.id == destinataire.id:
            await ctx.send(
                oops("Le payeur et le destinataire doivent être différents."), ephemeral=True
            )
            return

        repo = TricountRepository(guild_of(ctx).id)
        group = await repo.find_active_group(groupe)
        if not group:
            await ctx.send(
                oops(f"Aucun groupe actif trouvé avec le nom '{groupe}'."), ephemeral=True
            )
            return
        members = group["members"]
        balances = compute_balances(await repo.list_group_expenses(group["_id"]), members)

        def on_the_books(user_id: int) -> bool:
            # A current member, or a former member whose balance isn't settled.
            return user_id in members or bool(balances.get(user_id))

        if ctx.author.id not in members and ctx.author.id not in (payeur.id, destinataire.id):
            await ctx.send(
                oops("Il faut être membre du groupe, ou concerné par le remboursement."),
                ephemeral=True,
            )
            return
        for person in (payeur, destinataire):
            if not on_the_books(person.id):
                await ctx.send(
                    oops(f"{person.mention} n'est ni membre du groupe ni en attente de règlement."),
                    ephemeral=True,
                )
                return

        await repo.add_expense(
            {
                "group_id": group["_id"],
                "group_name": groupe,
                "kind": TRANSFER,
                "amount": round(montant, 2),
                "description": "Remboursement",
                "payer": payeur.id,
                "recipient": destinataire.id,
                # The recipient takes the whole amount: their credit goes down by
                # it and the payer's debt with it.
                "participants": [destinataire.id],
                "added_by": ctx.author.id,
                "date": clock.now(),
            }
        )
        after = compute_balances(await repo.list_group_expenses(group["_id"]), members)

        currency = guild_currency(guild_of(ctx).id)
        embed = card(
            "moduleTricount",
            "success",
            title="✅ Remboursement enregistré",
            description=(
                f"{payeur.mention} → {destinataire.mention} : "
                f"**{format_amount(to_cents(round(montant, 2)), currency)}** dans **{groupe}**"
            ),
        )
        for person in (payeur, destinataire):
            cents = after.get(person.id, 0)
            status = "à jour ✅" if cents == 0 else format_amount(cents, currency, signed=True)
            embed.add_field(name=f"Solde de {person.display_name}", value=status, inline=True)
        logger.info(
            "Remboursement de %.2f enregistré par %s dans le groupe Tricount '%s' (%s → %s)",
            montant,
            ctx.author.display_name,
            groupe,
            payeur.id,
            destinataire.id,
        )
        await ctx.send(embed=embed)

    @tricount_rembourser.autocomplete("groupe")
    async def rembourser_groupe_autocomplete(self, ctx: AutocompleteContext):
        # Every group: a former member settling up is no longer in the member list.
        await guild_group_autocomplete(
            ctx, TricountRepository.groups_collection, member_filter=False
        )
