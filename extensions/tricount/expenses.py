"""Expense add and modify commands."""

import os

from bson import ObjectId
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

from src.core import logging as logutil
from src.discord_ext.autocomplete import guild_group_autocomplete
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import fetch_user_safe, require_guild

from ._common import DEFAULT_CATEGORIES, DEFAULT_CATEGORY, expenses_col, groups_col

logger = logutil.init_logger(os.path.basename(__file__))


class ExpensesMixin:
    """Add and modify individual expenses within a group."""

    @slash_command(
        name="depense",
        description="Ajouter une dépense",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="montant",
        description="Montant de la dépense (en euros)",
        opt_type=OptionType.NUMBER,
        required=True,
    )
    @slash_option(
        name="description",
        description="Description de la dépense",
        opt_type=OptionType.STRING,
        required=True,
        max_length=100,
    )
    @slash_option(
        name="payeur",
        description="Qui a payé (par défaut: vous)",
        opt_type=OptionType.USER,
        required=False,
    )
    @slash_option(
        name="categorie",
        description="Catégorie (alimentation, transport, loisirs, …)",
        opt_type=OptionType.STRING,
        required=False,
        autocomplete=True,
    )
    async def depense(
        self,
        ctx: SlashContext,
        groupe: str,
        montant: float,
        description: str,
        payeur: User | Member | None = None,
        categorie: str | None = None,
    ):
        if not await require_guild(ctx):
            return

        if payeur is None:
            payeur = ctx.author

        if montant <= 0:
            await ctx.send("❌ Le montant doit être positif.", ephemeral=True)
            return

        group = await groups_col(ctx.guild.id).find_one({"name": groupe, "is_active": True})
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send(
                "❌ Vous devez être membre du groupe pour ajouter une dépense.", ephemeral=True
            )
            return
        if payeur.id not in group["members"]:
            await ctx.send("❌ Le payeur doit être membre du groupe.", ephemeral=True)
            return

        from datetime import datetime

        category = (categorie or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
        expense_data = {
            "group_id": group["_id"],
            "group_name": groupe,
            "amount": round(montant, 2),
            "description": description,
            "category": category,
            "payer": payeur.id,
            "added_by": ctx.author.id,
            "participants": group["members"],
            "date": datetime.now(),
        }
        await expenses_col(ctx.guild.id).insert_one(expense_data)

        embed = Embed(
            title="✅ Dépense ajoutée",
            description=f"Dépense ajoutée au groupe **{groupe}**",
            color=Colors.SUCCESS,
        )
        embed.add_field(name="Montant", value=f"{montant:.2f}€", inline=True)
        embed.add_field(name="Payeur", value=payeur.mention, inline=True)
        embed.add_field(name="Catégorie", value=category, inline=True)
        embed.add_field(name="Description", value=description, inline=False)
        embed.add_field(
            name="Part par personne", value=f"{montant / len(group['members']):.2f}€", inline=True
        )
        logger.info(
            "Dépense de %.2f€ ajoutée par %s au groupe '%s' (payeur: %s, catégorie: %s)",
            montant,
            ctx.author.display_name,
            groupe,
            payeur.display_name,
            category,
        )
        await ctx.send(embed=embed)

    @depense.autocomplete("categorie")
    async def depense_categorie_autocomplete(self, ctx: AutocompleteContext):
        query = (ctx.input_text or "").lower()
        seen: set[str] = set()
        choices: list[dict[str, str]] = []
        for cat in DEFAULT_CATEGORIES:
            if query in cat.lower() and cat not in seen:
                seen.add(cat)
                choices.append({"name": cat, "value": cat})
        # Surface previously used custom categories from this guild.
        if ctx.guild:
            try:
                used = await expenses_col(ctx.guild.id).distinct("category")
                for cat in used:
                    if not cat or cat in seen:
                        continue
                    if query in cat.lower():
                        seen.add(cat)
                        choices.append({"name": cat, "value": cat})
                    if len(choices) >= 25:
                        break
            except Exception:
                pass
        await ctx.send(choices=choices[:25])

    @depense.autocomplete("groupe")
    async def depense_groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, groups_col)

    @slash_command(
        name="modifier-depense",
        description="Modifier une dépense existante",
    )
    @slash_option(
        name="groupe",
        description="Nom du groupe",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="depense_id",
        description="ID de la dépense à modifier",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    @slash_option(
        name="nouveau_montant",
        description="Nouveau montant (laissez vide pour ne pas modifier)",
        opt_type=OptionType.NUMBER,
        required=False,
    )
    @slash_option(
        name="nouvelle_description",
        description="Nouvelle description (laissez vide pour ne pas modifier)",
        opt_type=OptionType.STRING,
        required=False,
        max_length=100,
    )
    @slash_option(
        name="nouveau_payeur",
        description="Nouveau payeur (laissez vide pour ne pas modifier)",
        opt_type=OptionType.USER,
        required=False,
    )
    async def modifier_depense(
        self,
        ctx: SlashContext,
        groupe: str,
        depense_id: str,
        nouveau_montant: float | None = None,
        nouvelle_description: str | None = None,
        nouveau_payeur: User | Member | None = None,
    ):
        if not await require_guild(ctx):
            return

        if nouveau_montant is not None and nouveau_montant <= 0:
            await ctx.send("❌ Le montant doit être positif.", ephemeral=True)
            return

        group = await groups_col(ctx.guild.id).find_one({"name": groupe, "is_active": True})
        if not group:
            await ctx.send(f"❌ Aucun groupe actif trouvé avec le nom '{groupe}'.", ephemeral=True)
            return
        if ctx.author.id not in group["members"]:
            await ctx.send(
                "❌ Vous devez être membre du groupe pour modifier une dépense.", ephemeral=True
            )
            return

        try:
            expense = await expenses_col(ctx.guild.id).find_one(
                {"_id": ObjectId(depense_id), "group_id": group["_id"]}
            )
        except Exception:
            await ctx.send("❌ ID de dépense invalide.", ephemeral=True)
            return

        if not expense:
            await ctx.send("❌ Dépense non trouvée dans ce groupe.", ephemeral=True)
            return

        if ctx.author.id != expense["added_by"] and ctx.author.id != expense["payer"]:
            await ctx.send(
                "❌ Vous ne pouvez modifier que les dépenses que vous avez ajoutées ou payées.",
                ephemeral=True,
            )
            return

        if nouveau_payeur and nouveau_payeur.id not in group["members"]:
            await ctx.send("❌ Le nouveau payeur doit être membre du groupe.", ephemeral=True)
            return

        modifications = {}
        changes_description = []

        if nouveau_montant is not None:
            modifications["amount"] = round(nouveau_montant, 2)
            changes_description.append(
                f"Montant: {expense['amount']:.2f}€ → {nouveau_montant:.2f}€"
            )
        if nouvelle_description is not None:
            modifications["description"] = nouvelle_description
            changes_description.append(
                f"Description: '{expense['description']}' → '{nouvelle_description}'"
            )
        if nouveau_payeur is not None:
            modifications["payer"] = nouveau_payeur.id
            old_payer_name, _ = await fetch_user_safe(self.bot, expense["payer"])
            changes_description.append(f"Payeur: {old_payer_name} → {nouveau_payeur.display_name}")

        if not modifications:
            await ctx.send("❌ Aucune modification spécifiée.", ephemeral=True)
            return

        await expenses_col(ctx.guild.id).update_one(
            {"_id": ObjectId(depense_id)}, {"$set": modifications}
        )

        embed = Embed(
            title="✅ Dépense modifiée",
            description=f"La dépense dans le groupe **{groupe}** a été modifiée.",
            color=Colors.SUCCESS,
        )
        embed.add_field(name="Modifications", value="\n".join(changes_description), inline=False)
        logger.info(
            "Dépense %s modifiée par %s dans le groupe '%s': %s",
            depense_id,
            ctx.author.display_name,
            groupe,
            ", ".join(changes_description),
        )
        await ctx.send(embed=embed)

    @modifier_depense.autocomplete("groupe")
    async def modifier_depense_groupe_autocomplete(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, groups_col)

    @modifier_depense.autocomplete("depense_id")
    async def modifier_depense_id_autocomplete(self, ctx: AutocompleteContext):
        if not ctx.guild:
            await ctx.send(choices=[])
            return

        input_text = ctx.input_text.lower()

        user_groups = (
            await groups_col(ctx.guild.id)
            .find({"is_active": True, "members": ctx.author.id})
            .to_list(length=None)
        )
        if not user_groups:
            await ctx.send(choices=[])
            return

        group_ids = [group["_id"] for group in user_groups]
        expenses = (
            await expenses_col(ctx.guild.id)
            .find(
                {
                    "group_id": {"$in": group_ids},
                    "$or": [{"added_by": ctx.author.id}, {"payer": ctx.author.id}],
                }
            )
            .sort("date", -1)
            .limit(25)
            .to_list(length=None)
        )

        choices = []
        for expense in expenses:
            group_name = "Groupe inconnu"
            for group in user_groups:
                if group["_id"] == expense["group_id"]:
                    group_name = group["name"]
                    break

            payer_name = f"ID:{expense['payer']}"
            try:
                payer = self.bot.get_user(expense["payer"])
                if payer:
                    payer_name = payer.display_name
            except Exception:
                pass

            display_name = f"[{group_name}] {expense['amount']:.2f}€ - {expense['description']} (par {payer_name})"
            if len(display_name) > 80:
                display_name = display_name[:77] + "..."

            if input_text in display_name.lower():
                choices.append({"name": display_name, "value": str(expense["_id"])})

        await ctx.send(choices=choices[:25])
