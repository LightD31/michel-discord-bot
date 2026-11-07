"""Tricount balances: per-expense participants, departed members, reimbursements, settling up."""

import asyncio
from types import SimpleNamespace

import pytest

import extensions.tricount.groups as groups_module
import extensions.tricount.reports as reports_module
from extensions.tricount.groups import GroupsMixin
from extensions.tricount.reports import ReportsMixin
from features.tricount import (
    TRANSFER,
    compute_balances,
    format_amount,
    settlement_plan,
    spending_total,
    to_cents,
    visible_balances,
)
from features.tricount.balances import split

A, B, C, D = 1, 2, 3, 4


def expense(amount, payer, participants=None, **extra):
    doc = {"amount": amount, "payer": payer, **extra}
    if participants is not None:
        doc["participants"] = participants
    return doc


def transfer(amount, payer, recipient):
    return expense(amount, payer, [recipient], kind=TRANSFER, recipient=recipient)


# ── Arithmetic ──────────────────────────────────────────────────────────


def test_cents_are_exact():
    assert to_cents(0.1 + 0.2) == 30
    assert to_cents(10.005) == 1001  # half up, not banker's rounding
    assert to_cents("12.5") == 1250
    assert to_cents(7) == 700


def test_split_hands_out_the_odd_cents():
    shares = split(1000, [A, B, C])
    assert shares == {A: 334, B: 333, C: 333}
    assert sum(shares.values()) == 1000


@pytest.mark.parametrize(
    ("cents", "signed", "text"),
    [(1234, False, "12.34€"), (1234, True, "+12.34€"), (-5, True, "-0.05€"), (0, True, "0.00€")],
)
def test_format_amount(cents, signed, text):
    assert format_amount(cents, "€", signed=signed) == text


# ── Balances ────────────────────────────────────────────────────────────


def test_each_expense_is_split_among_its_own_participants():
    expenses = [
        expense(90, A, [A, B, C]),  # before D joined
        expense(40, D, [A, B, C, D]),
    ]
    balances = compute_balances(expenses, current_members=[A, B, C, D])
    assert balances == {A: 6000 - 1000, B: -4000, C: -4000, D: 4000 - 1000}
    assert sum(balances.values()) == 0


def test_a_member_who_left_keeps_their_balance():
    """The reported bug: B left, and their share vanished from /bilan."""
    expenses = [expense(100, A, [A, B])]
    balances = compute_balances(expenses, current_members=[A])  # B has left
    assert balances == {A: 5000, B: -5000}
    assert visible_balances(balances, [A]) == {A: 5000, B: -5000}


def test_a_departed_payer_is_still_credited():
    expenses = [expense(60, B, [A, B, C])]
    balances = compute_balances(expenses, current_members=[A, C])
    assert balances[B] == 4000 and sum(balances.values()) == 0


def test_a_reimbursement_settles_and_hides_the_departed_member():
    expenses = [expense(100, A, [A, B]), transfer(50, B, A)]
    balances = compute_balances(expenses, current_members=[A])
    assert balances == {A: 0, B: 0}
    assert visible_balances(balances, [A]) == {A: 0}  # settled former member drops out


def test_an_expense_with_no_participants_stays_with_its_payer():
    """A recurring expense recorded once everyone had left the group."""
    balances = compute_balances([expense(30, A, [])], current_members=[B])
    assert balances == {A: 0, B: 0}


def test_spending_total_ignores_reimbursements():
    assert spending_total([expense(100, A, [A, B]), transfer(50, B, A)]) == 10000


# ── Settling up ─────────────────────────────────────────────────────────


def test_settlement_plan_clears_every_balance():
    balances = {A: 7000, B: -2000, C: -3000, D: -2000}
    plan = settlement_plan(balances)
    assert len(plan) <= len(balances) - 1
    after = dict(balances)
    for debtor, creditor, cents in plan:
        assert cents > 0
        after[debtor] += cents
        after[creditor] -= cents
    assert set(after.values()) == {0}
    assert plan == settlement_plan(dict(reversed(list(balances.items()))))  # stable


def test_nothing_to_settle():
    assert settlement_plan({A: 0, B: 0}) == []


# ── Commands (fake repository, no Discord) ──────────────────────────────


class FakeRepo:
    def __init__(self, group, expenses):
        self.group = group
        self.expenses = expenses

    async def find_active_group(self, name):
        return self.group if name == self.group["name"] else None

    async def list_group_expenses(self, group_id):
        return list(self.expenses)

    async def add_expense(self, data):
        self.expenses.append(data)

    async def remove_group_member(self, group_id, user_id):
        self.group["members"].remove(user_id)


def user(uid):
    return SimpleNamespace(id=uid, mention=f"<@{uid}>", display_name=f"user{uid}")


class Ctx:
    def __init__(self, author):
        self.author = user(author)
        self.guild = SimpleNamespace(id=99, name="srv")
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


@pytest.fixture
def repo(monkeypatch):
    group = {"_id": "g", "name": "coloc", "members": [A]}
    fake = FakeRepo(group, [expense(100, A, [A, B])])  # B left owing 50
    for module in (reports_module, groups_module):
        monkeypatch.setattr(module, "TricountRepository", lambda guild_id: fake)
    return fake


def field(embed, name):
    return next(f.value for f in embed.fields if f.name == name)


def test_bilan_shows_the_departed_member_and_how_to_settle(repo):
    ctx = Ctx(author=A)
    asyncio.run(ReportsMixin.bilan.callback(ReportsMixin(), ctx, "coloc"))
    embed = ctx.sent[-1]["embed"]
    balances = field(embed, "Balances")
    assert "<@1> : +50.00€" in balances
    assert "<@2> : -50.00€" in balances and "a quitté le groupe" in balances
    assert field(embed, "Pour solder") == "<@2> → <@1> : 50.00€"


def test_a_departed_member_can_see_what_they_owe(repo):
    ctx = Ctx(author=B)
    asyncio.run(ReportsMixin.bilan.callback(ReportsMixin(), ctx, "coloc"))
    assert "embed" in ctx.sent[-1]


def test_an_outsider_cannot_see_the_bilan(repo):
    ctx = Ctx(author=C)
    asyncio.run(ReportsMixin.bilan.callback(ReportsMixin(), ctx, "coloc"))
    assert ctx.sent[-1].get("ephemeral") and "membre" in ctx.sent[-1]["content"]


def test_the_departed_member_settles_up_themselves(repo):
    ctx = Ctx(author=B)
    asyncio.run(
        GroupsMixin.tricount_rembourser.callback(GroupsMixin(), ctx, "coloc", user(A), 50.0)
    )
    recorded = repo.expenses[-1]
    assert recorded["kind"] == TRANSFER and recorded["payer"] == B and recorded["recipient"] == A
    assert compute_balances(repo.expenses, repo.group["members"]) == {A: 0, B: 0}
    embed = ctx.sent[-1]["embed"]
    assert all(f.value == "à jour ✅" for f in embed.fields)


def test_reimbursement_refuses_strangers_and_self_payment(repo):
    ctx = Ctx(author=C)  # neither member nor on the books
    asyncio.run(
        GroupsMixin.tricount_rembourser.callback(GroupsMixin(), ctx, "coloc", user(A), 10.0)
    )
    assert ctx.sent[-1].get("ephemeral")
    ctx = Ctx(author=A)
    asyncio.run(
        GroupsMixin.tricount_rembourser.callback(GroupsMixin(), ctx, "coloc", user(A), 10.0)
    )
    assert "différents" in ctx.sent[-1]["content"]
    assert len(repo.expenses) == 1  # nothing recorded


def test_leaving_with_an_open_balance_warns(repo):
    repo.group["members"] = [A, B]
    ctx = Ctx(author=B)
    asyncio.run(GroupsMixin.tricount_quitter.callback(GroupsMixin(), ctx, "coloc"))
    embed = ctx.sent[-1]["embed"]
    assert "-50.00€" in embed.description and "/tricount rembourser" in embed.description
    assert repo.group["members"] == [A]
