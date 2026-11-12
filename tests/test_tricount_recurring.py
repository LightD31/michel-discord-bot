"""Recurring Tricount expenses: creation, the tick that books them, and stopping (T04)."""

from datetime import timedelta

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

import extensions.tricount.recurring as recurring_module
from extensions.tricount import TricountExtension
from src.core import clock


class MemoryTricount:
    def __init__(self):
        self.groups = {"G": {"_id": "G", "name": "Coloc", "members": [3, 4], "active": True}}
        self.recurring: dict[str, dict] = {}
        self.expenses: list[dict] = []

    async def find_active_group(self, name):
        return next((g for g in self.groups.values() if g["name"] == name and g["active"]), None)

    async def find_active_group_by_id(self, group_id):
        group = self.groups.get(group_id)
        return group if group and group["active"] else None

    async def add_recurring(self, data):
        rid = f"r{len(self.recurring) + 1}"
        self.recurring[rid] = {**data, "_id": rid}
        return rid

    async def list_active_recurring(self, user_id, group_name=None):
        return [
            d
            for d in self.recurring.values()
            if d["active"]
            and d["added_by"] == user_id
            and (group_name is None or d["group_name"] == group_name)
        ]

    async def list_due_recurring(self, now):
        return [d for d in self.recurring.values() if d["active"] and d["next_run"] <= now]

    async def claim_recurring(self, doc, next_run):
        stored = self.recurring[doc["_id"]]
        if stored["next_run"] != doc["next_run"]:
            return False
        stored["next_run"] = next_run
        return True

    async def add_expense(self, data):
        self.expenses.append(data)

    async def stop_recurring(self, recurring_id, user_id):
        doc = self.recurring.get(str(recurring_id))
        if not doc or not doc["active"] or doc["added_by"] != user_id:
            return 0
        doc["active"] = False
        return 1

    async def deactivate_recurring(self, recurring_id):
        self.recurring[recurring_id]["active"] = False


@pytest.fixture
def setup(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleTricount": {"enabled": True}}}})
    repo = MemoryTricount()
    monkeypatch.setattr(recurring_module, "TricountRepository", lambda guild_id: repo)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    payer = guild.add_member(FakeMember(3, "alice"))
    ctx = FakeCtx(payer, channel)
    ext = bare_extension(TricountExtension, ctx.bot)
    return ext, ctx, repo


async def add(ext, ctx, frequency="monthly", amount=600.0, group="Coloc"):
    await run(
        TricountExtension.depense_recurrente_ajouter,
        ext,
        ctx,
        group,
        amount,
        "Loyer",
        frequency,
    )


async def test_a_member_schedules_an_expense_for_next_month(setup):
    ext, ctx, repo = setup
    await add(ext, ctx)
    (doc,) = repo.recurring.values()
    assert doc["amount"] == 600.0 and doc["frequency"] == "monthly" and doc["active"]
    assert doc["next_run"] > clock.now() + timedelta(days=27)
    assert doc["next_run"].utcoffset() == timedelta(0)
    embed = ctx.replies[-1]["embed"]
    shown = f"{clock.to_local(doc['next_run']):%d/%m/%Y %H:%M}"
    assert shown in embed.description  # on the Paris clock, not in UTC


@pytest.mark.parametrize(("amount", "group"), [(-5.0, "Coloc"), (10.0, "Inconnu")])
async def test_bad_amounts_and_unknown_groups_are_refused(setup, amount, group):
    ext, ctx, repo = setup
    await add(ext, ctx, amount=amount, group=group)
    assert ctx.last.startswith("❌") and repo.recurring == {}


async def test_a_non_member_cannot_schedule_in_the_group(setup):
    ext, ctx, repo = setup
    outsider = ctx.guild.add_member(FakeMember(9, "eve"))
    other = FakeCtx(outsider, ctx.channel, bot=ctx.bot)
    await add(ext, other)
    assert "non membre" in other.last and repo.recurring == {}


async def test_the_tick_books_a_due_expense_once_and_moves_it_on(setup):
    ext, ctx, repo = setup
    await add(ext, ctx, frequency="weekly")
    (doc,) = repo.recurring.values()
    doc["next_run"] = clock.now() - timedelta(minutes=1)

    await TricountExtension.recurring_tick.callback(ext)
    await TricountExtension.recurring_tick.callback(ext)

    (expense,) = repo.expenses
    assert expense["amount"] == 600.0 and expense["participants"] == [3, 4]
    assert expense["description"] == "🔁 Loyer"
    assert doc["next_run"] > clock.now() + timedelta(days=6)


async def test_a_recurrence_claimed_elsewhere_books_nothing(setup):
    ext, ctx, repo = setup
    await add(ext, ctx)
    (doc,) = repo.recurring.values()
    doc["next_run"] = clock.now() - timedelta(minutes=1)
    repo.claim_recurring = _lost
    await TricountExtension.recurring_tick.callback(ext)
    assert repo.expenses == []


async def _lost(doc, next_run):
    return False


async def test_a_deleted_group_stops_its_recurrences(setup):
    ext, ctx, repo = setup
    await add(ext, ctx)
    (doc,) = repo.recurring.values()
    doc["next_run"] = clock.now() - timedelta(minutes=1)
    repo.groups["G"]["active"] = False
    await TricountExtension.recurring_tick.callback(ext)
    assert not doc["active"] and repo.expenses == []


async def test_list_and_stop(setup):
    ext, ctx, repo = setup
    await add(ext, ctx)
    await run(TricountExtension.depense_recurrente_lister, ext, ctx)
    (field,) = ctx.replies[-1]["embed"].fields
    assert "Loyer" in field.name

    await run(TricountExtension.depense_recurrente_arreter, ext, ctx, "r1")
    assert ctx.last == "✅ Récurrence arrêtée."
    await run(TricountExtension.depense_recurrente_arreter, ext, ctx, "r1")
    assert "déjà arrêtée" in ctx.last
