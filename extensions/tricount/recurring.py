"""Recurring expenses: scheduled templates that re-create an expense on cadence."""

import os
from datetime import UTC, datetime, timedelta

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
from src.core import clock
from src.core import logging as logutil
from src.core.logging import log_failure
from src.discord_ext.autocomplete import guild_group_autocomplete
from src.discord_ext.embeds import Colors
from src.discord_ext.messages import require_guild
from src.discord_ext.scopes import guild_scopes

from ._common import (
    DEFAULT_CATEGORIES,
    DEFAULT_CATEGORY,
    TRICOUNT,
)

logger = logutil.init_logger(__name__)


_STEPS: dict[str, timedelta | relativedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": relativedelta(months=1),
    "yearly": relativedelta(years=1),
}


def _next_occurrence(remind_time: datetime, frequency: str) -> datetime:
    """One period after *remind_time*, counted on the community's wall clock."""
    step = _STEPS.get(frequency)
    if step is None:
        raise ValueError(f"Unknown frequency: {frequency}")
    wall = clock.to_local(remind_time).replace(tzinfo=None) + step
    return wall.replace(tzinfo=clock.LOCAL_TZ).astimezone(UTC)


class RecurringMixin:
    """Manage recurring expense templates."""

    @listen()
    async def on_startup(self):
        # Indexes are best-effort; failure here shouldn't break extension load.
        for guild_id in TRICOUNT.guild_ids():
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
        next_run = _next_occurrence(clock.now(), frequence)
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
            "created_at": clock.now(),
        }
        inserted_id = await repo.add_recurring(doc)
        embed = Embed(
            title="✅ Dépense récurrente créée",
            description=(
                f"**{description}** — {montant:.2f}€ ({frequence})\n"
                f"Prochaine occurrence : {clock.to_local(next_run):%d/%m/%Y %H:%M}"
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
                    f"Prochaine : {clock.to_local(doc['next_run']):%d/%m/%Y %H:%M}\n"
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
        modified_count = await TricountRepository(ctx.guild.id).stop_recurring(
            recurrence_id, ctx.author.id
        )
        if modified_count == 0:
            await ctx.send("❌ Récurrence introuvable ou déjà arrêtée.", ephemeral=True)
            return
        await ctx.send("✅ Récurrence arrêtée.", ephemeral=True)

    @Task.create(IntervalTrigger(minutes=5))
    async def recurring_tick(self):
        now = clock.now()
        for guild_id in TRICOUNT.guild_ids():
            try:
                due = await TricountRepository(guild_id).list_due_recurring(now)
            except Exception as e:
                log_failure(logger, "Could not list due recurring expenses for %s: %s", guild_id, e)
                continue
            for doc in due:
                await self._materialise_recurring(guild_id, doc)

    async def _materialise_recurring(self, guild_id: str, doc: dict) -> None:
        """Reschedule the recurrence, then create the expense it was due for.

        The recurrence is claimed first (moved to its next date only if it
        still holds this one), so the expense is created once: two runs can't
        both create it, and a failed write creates nothing.
        """
        repo = TricountRepository(guild_id)
        try:
            group = await repo.find_active_group_by_id(doc["group_id"])
            if not group:
                # Group was deleted — deactivate the recurrence.
                await repo.deactivate_recurring(doc["_id"])
                return
            next_run = _next_occurrence(doc["next_run"], doc["frequency"])
            while next_run <= clock.now():
                next_run = _next_occurrence(next_run, doc["frequency"])
            if not await repo.claim_recurring(doc, next_run):
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
                "date": clock.now(),
                "recurring_id": doc["_id"],
            }
            await repo.add_expense(expense)
        except Exception as e:
            log_failure(logger, "Failed to materialise recurring expense %s: %s", doc.get("_id"), e)
