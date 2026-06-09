"""Recurring expenses: scheduled templates that re-create an expense on cadence."""

import os
from datetime import datetime, timedelta

from bson import ObjectId
from dateutil.relativedelta import relativedelta
from interactions import (
    AutocompleteContext,
    Embed,
    IntervalTrigger,
    OptionType,
    SlashCommandChoice,
    SlashContext,
    Task,
    listen,
    slash_command,
    slash_option,
)

from features.tricount import TricountRepository
from src.core import logging as logutil
from src.discord_ext.autocomplete import guild_group_autocomplete
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild

from ._common import (
    DEFAULT_CATEGORIES,
    DEFAULT_CATEGORY,
    enabled_servers,
)

logger = logutil.init_logger(os.path.basename(__file__))


def _next_occurrence(remind_time: datetime, frequency: str) -> datetime:
    if frequency == "daily":
        return remind_time + timedelta(days=1)
    if frequency == "weekly":
        return remind_time + timedelta(weeks=1)
    if frequency == "monthly":
        return remind_time + relativedelta(months=1)
    if frequency == "yearly":
        return remind_time + relativedelta(years=1)
    raise ValueError(f"Unknown frequency: {frequency}")


class RecurringMixin:
    """Manage recurring expense templates."""

    @listen()
    async def on_startup(self):
        # Indexes are best-effort; failure here shouldn't break extension load.
        for guild_id in enabled_servers:
            try:
                await TricountRepository(guild_id).ensure_recurring_indexes()
            except Exception as e:
                logger.debug("Could not init recurring indexes for %s: %s", guild_id, e)
        self.recurring_tick.start()

    @slash_command(
        name="depense-recurrente",
        description="Programmer une dépense récurrente",
        sub_cmd_name="ajouter",
        sub_cmd_description="Créer une dépense récurrente",
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
        description="Montant (€)",
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
        name="frequence",
        description="Fréquence",
        opt_type=OptionType.STRING,
        required=True,
        choices=[
            SlashCommandChoice(name="Quotidien", value="daily"),
            SlashCommandChoice(name="Hebdomadaire", value="weekly"),
            SlashCommandChoice(name="Mensuel", value="monthly"),
            SlashCommandChoice(name="Annuel", value="yearly"),
        ],
    )
    @slash_option(
        name="categorie",
        description="Catégorie (par défaut: Autre)",
        opt_type=OptionType.STRING,
        required=False,
        autocomplete=True,
    )
    async def depense_recurrente_ajouter(
        self,
        ctx: SlashContext,
        groupe: str,
        montant: float,
        description: str,
        frequence: str,
        categorie: str | None = None,
    ):
        if not await require_guild(ctx):
            return
        if montant <= 0:
            await ctx.send("❌ Le montant doit être positif.", ephemeral=True)
            return

        repo = TricountRepository(ctx.guild.id)
        group = await repo.find_active_group(groupe)
        if not group or ctx.author.id not in group["members"]:
            await ctx.send("❌ Groupe introuvable ou non membre.", ephemeral=True)
            return

        category = (categorie or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
        next_run = _next_occurrence(datetime.now(), frequence)
        doc = {
            "group_id": group["_id"],
            "group_name": groupe,
            "amount": round(montant, 2),
            "description": description,
            "category": category,
            "payer": ctx.author.id,
            "added_by": ctx.author.id,
            "frequency": frequence,
            "next_run": next_run,
            "active": True,
            "created_at": datetime.now(),
        }
        inserted_id = await repo.add_recurring(doc)
        embed = Embed(
            title="✅ Dépense récurrente créée",
            description=(
                f"**{description}** — {montant:.2f}€ ({frequence})\n"
                f"Prochaine occurrence : {next_run:%d/%m/%Y %H:%M}"
            ),
            color=Colors.SUCCESS,
        )
        embed.set_footer(text=f"ID : {inserted_id}")
        await ctx.send(embed=embed)

    @depense_recurrente_ajouter.autocomplete("groupe")
    async def _ajouter_groupe_ac(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, TricountRepository.groups_collection)

    @depense_recurrente_ajouter.autocomplete("categorie")
    async def _ajouter_categorie_ac(self, ctx: AutocompleteContext):
        query = (ctx.input_text or "").lower()
        choices = [
            {"name": cat, "value": cat} for cat in DEFAULT_CATEGORIES if query in cat.lower()
        ]
        await ctx.send(choices=choices[:25])

    @depense_recurrente_ajouter.subcommand(
        sub_cmd_name="lister",
        sub_cmd_description="Voir vos dépenses récurrentes actives",
    )
    @slash_option(
        name="groupe",
        description="Filtrer par groupe",
        opt_type=OptionType.STRING,
        required=False,
        autocomplete=True,
    )
    async def depense_recurrente_lister(self, ctx: SlashContext, groupe: str | None = None):
        if not await require_guild(ctx):
            return
        docs = await TricountRepository(ctx.guild.id).list_active_recurring(ctx.author.id, groupe)
        if not docs:
            await ctx.send("Aucune dépense récurrente active.", ephemeral=True)
            return
        embed = Embed(
            title="🔁 Dépenses récurrentes",
            description=f"{len(docs)} dépense(s) active(s)",
            color=Colors.INFO,
        )
        for doc in docs[:10]:
            embed.add_field(
                name=f"{doc['description']} — {doc['amount']:.2f}€",
                value=(
                    f"Groupe : **{doc['group_name']}** · {doc['frequency']}\n"
                    f"Catégorie : {doc.get('category', DEFAULT_CATEGORY)}\n"
                    f"Prochaine : {doc['next_run']:%d/%m/%Y %H:%M}\n"
                    f"ID : `{doc['_id']}`"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @depense_recurrente_lister.autocomplete("groupe")
    async def _lister_groupe_ac(self, ctx: AutocompleteContext):
        await guild_group_autocomplete(ctx, TricountRepository.groups_collection)

    @depense_recurrente_ajouter.subcommand(
        sub_cmd_name="arreter",
        sub_cmd_description="Désactiver une dépense récurrente",
    )
    @slash_option(
        name="recurrence_id",
        description="ID de la récurrence à arrêter",
        opt_type=OptionType.STRING,
        required=True,
    )
    async def depense_recurrente_arreter(self, ctx: SlashContext, recurrence_id: str):
        if not await require_guild(ctx):
            return
        try:
            obj_id = ObjectId(recurrence_id)
        except Exception:
            await ctx.send("❌ ID invalide.", ephemeral=True)
            return
        modified_count = await TricountRepository(ctx.guild.id).stop_recurring(
            obj_id, ctx.author.id
        )
        if modified_count == 0:
            await ctx.send("❌ Récurrence introuvable ou déjà arrêtée.", ephemeral=True)
            return
        await ctx.send("✅ Récurrence arrêtée.", ephemeral=True)

    @Task.create(IntervalTrigger(minutes=5))
    async def recurring_tick(self):
        now = datetime.now()
        for guild_id in enabled_servers:
            try:
                due = await TricountRepository(guild_id).list_due_recurring(now)
            except Exception as e:
                logger.error("Could not list due recurring expenses for %s: %s", guild_id, e)
                continue
            for doc in due:
                await self._materialise_recurring(guild_id, doc)

    async def _materialise_recurring(self, guild_id: str, doc: dict) -> None:
        """Create the next concrete expense and reschedule the recurrence."""
        repo = TricountRepository(guild_id)
        try:
            group = await repo.find_active_group_by_id(doc["group_id"])
            if not group:
                # Group was deleted — deactivate the recurrence.
                await repo.deactivate_recurring(doc["_id"])
                return
            expense = {
                "group_id": group["_id"],
                "group_name": group["name"],
                "amount": doc["amount"],
                "description": f"🔁 {doc['description']}",
                "category": doc.get("category", DEFAULT_CATEGORY),
                "payer": doc["payer"],
                "added_by": doc["added_by"],
                "participants": group["members"],
                "date": datetime.now(),
                "recurring_id": doc["_id"],
            }
            await repo.add_expense(expense)
            next_run = _next_occurrence(doc["next_run"], doc["frequency"])
            while next_run <= datetime.now():
                next_run = _next_occurrence(next_run, doc["frequency"])
            await repo.reschedule_recurring(doc["_id"], next_run)
        except Exception as e:
            logger.error("Failed to materialise recurring expense %s: %s", doc.get("_id"), e)
