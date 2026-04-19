"""Expense reports: list expenses, balance, and group overview."""

import os

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
from src.discord_ext.messages import fetch_user_safe, require_guild

from ._common import expenses_col, groups_col

logger = logutil.init_logger(os.path.basename(__file__))


class ReportsMixin:
    """Query expense data: list, balance, and personal group overview."""

    @slash_command(
        name="liste-depenses",
        description="Voir la liste des dépenses d'un groupe",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="limite",
        description="Nombre de dépenses à afficher (défaut: 10, max: 20)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=1,
        max_value=20,
    )
    async def liste_depenses(self, ctx: SlashContext, groupe: str, limite: int | None = 10):
        if not await require_guild(ctx):
            return

        group = await groups_col(ctx.guild.id).find_one({"name": groupe, "is_active": True})
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send(
                "❌ Vous devez être membre du groupe pour voir les dépenses.", ephemeral=True
            )
            return

        limit_value = limite if limite is not None else 10
        expenses = (
            await expenses_col(ctx.guild.id)
            .find({"group_id": group["_id"]})
            .sort("date", -1)
            .limit(limit_value)
            .to_list(length=None)
        )

        if not expenses:
            embed = Embed(
                title=f"📋 Dépenses du groupe {groupe}",
                description="Aucune dépense enregistrée.",
                color=Colors.INFO,
            )
            await ctx.send(embed=embed)
            return

        embed = Embed(
            title=f"📋 Dépenses du groupe {groupe}",
            description=f"Dernières {len(expenses)} dépense(s):",
            color=Colors.INFO,
        )
        for i, expense in enumerate(expenses, 1):
            payer_name, _ = await fetch_user_safe(self.bot, expense["payer"])
            added_by_name, _ = await fetch_user_safe(self.bot, expense["added_by"])
            date_str = expense["date"].strftime("%d/%m/%Y %H:%M")
            field_value = (
                f"**Montant:** {expense['amount']:.2f}€\n"
                f"**Payeur:** {payer_name}\n"
                f"**Ajouté par:** {added_by_name}\n"
                f"**Date:** {date_str}\n"
                f"**ID:** `{expense['_id']}`"
            )
            embed.add_field(name=f"{i}. {expense['description']}", value=field_value, inline=True)

        embed.set_footer(text="💡 Utilisez l'ID pour modifier une dépense avec /modifier-depense")
        await ctx.send(embed=embed)

    @liste_depenses.autocomplete("groupe")
    async def liste_depenses_groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, groups_col)

    @slash_command(
        name="bilan",
        description="Voir le bilan des dépenses d'un groupe",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def bilan(self, ctx: SlashContext, groupe: str):
        if not await require_guild(ctx):
            return

        group = await groups_col(ctx.guild.id).find_one({"name": groupe, "is_active": True})
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send(
                "❌ Vous devez être membre du groupe pour voir le bilan.", ephemeral=True
            )
            return

        expenses = (
            await expenses_col(ctx.guild.id)
            .find({"group_id": group["_id"]})
            .to_list(length=None)
        )

        if not expenses:
            embed = Embed(
                title=f"📊 Bilan du groupe {groupe}",
                description="Aucune dépense enregistrée.",
                color=Colors.INFO,
            )
            await ctx.send(embed=embed)
            return

        total_expenses = sum(expense["amount"] for expense in expenses)
        num_members = len(group["members"])
        cost_per_person = total_expenses / num_members

        balances = {}
        for member_id in group["members"]:
            paid = sum(expense["amount"] for expense in expenses if expense["payer"] == member_id)
            owes = cost_per_person
            balance = paid - owes
            balances[member_id] = {"paid": paid, "owes": owes, "balance": balance}

        embed = Embed(
            title=f"📊 Bilan du groupe {groupe}",
            description=f"**Total des dépenses:** {total_expenses:.2f}€\n**Coût par personne:** {cost_per_person:.2f}€",
            color=Colors.INFO,
        )

        balance_text = ""
        for member_id, data in balances.items():
            _, user = await fetch_user_safe(self.bot, member_id)
            if user:
                if data["balance"] > 0.01:
                    balance_text += f"💰 {user.mention}: +{data['balance']:.2f}€ (a payé {data['paid']:.2f}€)\n"
                elif data["balance"] < -0.01:
                    balance_text += f"💸 {user.mention}: {data['balance']:.2f}€ (a payé {data['paid']:.2f}€)\n"
                else:
                    balance_text += f"✅ {user.mention}: 0€ (a payé {data['paid']:.2f}€)\n"
            else:
                balance_text += f"❓ Utilisateur inconnu: {data['balance']:.2f}€\n"

        embed.add_field(name="Balances", value=balance_text or "Aucune donnée", inline=False)
        await ctx.send(embed=embed)

    @bilan.autocomplete("groupe")
    async def bilan_groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, groups_col)

    @slash_command(
        name="mes-groupes",
        description="Voir tous vos groupes Tricount",
    )
    async def mes_groupes(self, ctx: SlashContext):
        if not await require_guild(ctx):
            return

        groups = (
            await groups_col(ctx.guild.id)
            .find({"is_active": True, "members": ctx.author.id})
            .to_list(length=None)
        )

        if not groups:
            embed = Embed(
                title="📝 Mes groupes Tricount",
                description="Vous n'êtes membre d'aucun groupe.",
                color=Colors.INFO,
            )
            await ctx.send(embed=embed)
            return

        embed = Embed(
            title="📝 Mes groupes Tricount",
            description=f"Vous êtes membre de {len(groups)} groupe(s):",
            color=Colors.INFO,
        )
        for group in groups[:10]:
            expense_count = await expenses_col(ctx.guild.id).count_documents(
                {"group_id": group["_id"]}
            )
            expenses_for_group = (
                await expenses_col(ctx.guild.id)
                .find({"group_id": group["_id"]})
                .to_list(length=None)
            )
            total_amount = sum(expense["amount"] for expense in expenses_for_group)

            field_value = (
                f"**Membres:** {len(group['members'])}\n"
                f"**Dépenses:** {expense_count}\n"
                f"**Total:** {total_amount:.2f}€\n"
            )
            if group["description"]:
                field_value += f"**Description:** {group['description']}"

            embed.add_field(name=group["name"], value=field_value, inline=True)

        await ctx.send(embed=embed)
