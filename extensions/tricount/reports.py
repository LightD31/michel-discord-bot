"""Expense reports: list expenses, balance, and group overview."""

import asyncio
import os
from collections import defaultdict

from interactions import (
    AutocompleteContext,
    Buckets,
    Embed,
    File,
    OptionType,
    SlashContext,
    cooldown,
    slash_command,
    slash_option,
)

from features.tricount import (
    TricountRepository,
    compute_balances,
    format_amount,
    is_transfer,
    render_category_chart,
    settlement_plan,
    spending_total,
    to_cents,
    visible_balances,
)
from src.core import clock
from src.core import logging as logutil
from src.core.logging import log_failure
from src.discord_ext.autocomplete import guild_group_autocomplete
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import fetch_user_safe, require_guild
from src.discord_ext.scopes import guild_scopes

from ._common import DEFAULT_CATEGORY, TRICOUNT, guild_currency

logger = logutil.init_logger(__name__)

# Discord caps an embed field's value at 1024 characters.
_FIELD_LIMIT = 1024


def _field_value(lines: list[str]) -> str:
    """Join *lines*, dropping the ones that don't fit in a field (with a note)."""
    kept: list[str] = []
    size = 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > _FIELD_LIMIT - 20:
            kept.append(f"… et {len(lines) - i} de plus")
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept) or "Aucune donnée"


class ReportsMixin:
    """Query expense data: list, balance, and personal group overview."""

    @slash_command(
        name="liste-depenses",
        description="Voir la liste des dépenses d'un groupe",
        scopes=guild_scopes(TRICOUNT.guild_ids()),
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

        repo = TricountRepository(ctx.guild.id)
        group = await repo.find_active_group(groupe)
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send(
                "❌ Vous devez être membre du groupe pour voir les dépenses.", ephemeral=True
            )
            return

        limit_value = limite if limite is not None else 10
        expenses = await repo.list_recent_expenses(group["_id"], limit_value)

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
        currency = guild_currency(ctx.guild.id)
        for i, expense in enumerate(expenses, 1):
            payer_name, _ = await fetch_user_safe(self.bot, expense["payer"])
            added_by_name, _ = await fetch_user_safe(self.bot, expense["added_by"])
            date_str = clock.to_local(expense["date"]).strftime("%d/%m/%Y %H:%M")
            amount = format_amount(to_cents(expense["amount"]), currency)
            if is_transfer(expense):
                recipient_name, _ = await fetch_user_safe(self.bot, expense["recipient"])
                title = f"{i}. ↔️ Remboursement"
                field_value = (
                    f"**Montant:** {amount}\n"
                    f"**De:** {payer_name} → **À:** {recipient_name}\n"
                    f"**Ajouté par:** {added_by_name}\n"
                    f"**Date:** {date_str}\n"
                    f"**ID:** `{expense['_id']}`"
                )
            else:
                title = f"{i}. {expense['description']}"
                field_value = (
                    f"**Montant:** {amount}\n"
                    f"**Payeur:** {payer_name}\n"
                    f"**Ajouté par:** {added_by_name}\n"
                    f"**Date:** {date_str}\n"
                    f"**ID:** `{expense['_id']}`"
                )
            embed.add_field(name=title, value=field_value, inline=True)

        embed.set_footer(text="💡 Utilisez l'ID pour modifier une dépense avec /modifier-depense")
        await ctx.send(embed=embed)

    @liste_depenses.autocomplete("groupe")
    async def liste_depenses_groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, TricountRepository.groups_collection)

    @slash_command(
        name="bilan",
        description="Voir le bilan des dépenses d'un groupe",
        scopes=guild_scopes(TRICOUNT.guild_ids()),
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

        repo = TricountRepository(ctx.guild.id)
        group = await repo.find_active_group(groupe)
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return

        expenses = await repo.list_group_expenses(group["_id"])
        members = group["members"]
        balances = compute_balances(expenses, members)
        # Former members still on the books may check what they owe or are owed.
        if ctx.author.id not in members and not balances.get(ctx.author.id):
            await ctx.send(
                "❌ Vous devez être membre du groupe pour voir le bilan.", ephemeral=True
            )
            return

        if not expenses:
            embed = Embed(
                title=f"📊 Bilan du groupe {groupe}",
                description="Aucune dépense enregistrée.",
                color=Colors.INFO,
            )
            await ctx.send(embed=embed)
            return

        currency = guild_currency(ctx.guild.id)
        paid: dict[int, int] = defaultdict(int)
        for expense in expenses:
            if not is_transfer(expense):
                paid[expense["payer"]] += to_cents(expense.get("amount", 0))

        embed = Embed(
            title=f"📊 Bilan du groupe {groupe}",
            description=(
                f"**Total des dépenses :** {format_amount(spending_total(expenses), currency)}"
            ),
            color=Colors.INFO,
        )

        lines = []
        shown = visible_balances(balances, members)
        for person, cents in sorted(shown.items(), key=lambda item: -item[1]):
            icon = "💰" if cents > 0 else ("💸" if cents < 0 else "✅")
            line = (
                f"{icon} <@{person}> : {format_amount(cents, currency, signed=True)} "
                f"(a payé {format_amount(paid[person], currency)})"
            )
            if person not in members:
                line += " — *a quitté le groupe*"
            lines.append(line)
        embed.add_field(name="Balances", value=_field_value(lines), inline=False)

        plan = settlement_plan(balances)
        transfers = [
            f"<@{debtor}> → <@{creditor}> : {format_amount(cents, currency)}"
            for debtor, creditor, cents in plan
        ]
        embed.add_field(
            name="Pour solder",
            value=_field_value(transfers) if transfers else "Tout le monde est à jour ✅",
            inline=False,
        )
        if transfers:
            embed.set_footer(
                text="💡 Une fois payé, enregistrez-le avec /tricount rembourser. "
                "Un membre qui quitte le groupe reste au bilan tant que son solde n'est pas réglé."
            )
        await ctx.send(embed=embed)

    @bilan.autocomplete("groupe")
    async def bilan_groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, TricountRepository.groups_collection)

    @slash_command(
        name="tricount-graphique",
        description="Exporter un graphique des dépenses par catégorie",
        scopes=guild_scopes(TRICOUNT.guild_ids()),
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @cooldown(Buckets.USER, 1, 15)
    async def tricount_graphique(self, ctx: SlashContext, groupe: str):
        if not await require_guild(ctx):
            return
        repo = TricountRepository(ctx.guild.id)
        group = await repo.find_active_group(groupe)
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send(
                "❌ Vous devez être membre du groupe pour voir le graphique.",
                ephemeral=True,
            )
            return

        await ctx.defer()
        expenses = await repo.list_group_expenses(group["_id"])
        if not expenses:
            await ctx.send("Aucune dépense enregistrée dans ce groupe.")
            return

        totals: dict[str, float] = defaultdict(float)
        for expense in expenses:
            if is_transfer(expense):
                continue  # a reimbursement is not spending
            cat = expense.get("category") or DEFAULT_CATEGORY
            totals[cat] += to_cents(expense.get("amount", 0)) / 100

        try:
            buffer = await asyncio.to_thread(  # Pillow work, off the event loop
                render_category_chart,
                title=f"Dépenses du groupe {groupe}",
                category_totals=dict(totals),
                currency=guild_currency(ctx.guild.id),
            )
        except Exception as e:
            log_failure(logger, "Could not render tricount chart: %s", e)
            await ctx.send("❌ Erreur lors de la génération du graphique.")
            return
        await ctx.send(file=File(file=buffer, file_name="tricount.png"))

    @tricount_graphique.autocomplete("groupe")
    async def graphique_groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, TricountRepository.groups_collection)

    @slash_command(
        name="mes-groupes",
        description="Voir tous vos groupes Tricount",
        scopes=guild_scopes(TRICOUNT.guild_ids()),
    )
    async def mes_groupes(self, ctx: SlashContext):
        if not await require_guild(ctx):
            return

        repo = TricountRepository(ctx.guild.id)
        groups = await repo.list_member_groups(ctx.author.id)

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
        currency = guild_currency(ctx.guild.id)
        for group in groups[:10]:
            expenses_for_group = await repo.list_group_expenses(group["_id"])
            spending = [e for e in expenses_for_group if not is_transfer(e)]
            mine = compute_balances(expenses_for_group, group["members"]).get(ctx.author.id, 0)

            field_value = (
                f"**Membres:** {len(group['members'])}\n"
                f"**Dépenses:** {len(spending)}\n"
                f"**Total:** {format_amount(spending_total(spending), currency)}\n"
                f"**Mon solde:** {format_amount(mine, currency, signed=True)}\n"
            )
            if group["description"]:
                field_value += f"**Description:** {group['description']}"

            embed.add_field(name=group["name"], value=field_value, inline=True)

        await ctx.send(embed=embed)
