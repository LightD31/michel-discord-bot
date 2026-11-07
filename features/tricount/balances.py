"""Who owes what in a Tricount group, in integer cents.

Each expense is split equally among its ``participants``: the group's members
when the expense was recorded, stored on the expense. A member who leaves the
group therefore keeps the share of every expense they took part in, and keeps
the credit for what they paid, until the balance is settled. A member who joins
later shares only the expenses recorded after they joined.

A reimbursement is stored as an expense of ``kind`` :data:`TRANSFER`: the payer
gave the amount to a single participant, the recipient. The same arithmetic then
settles the debt, while transfers stay out of the spending totals and the
category chart.

Pure functions over the stored dicts, so they can be tested without MongoDB or
Discord.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

TRANSFER = "transfer"


def to_cents(amount: Any) -> int:
    """A stored amount (float, int or str) as integer cents, rounded half up."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def is_transfer(expense: Mapping[str, Any]) -> bool:
    return expense.get("kind") == TRANSFER


def _participants(expense: Mapping[str, Any]) -> list[int]:
    """Who shares *expense*: its stored participants, or the payer alone if none.

    A recurring expense of a group nobody is left in is stored with no
    participants; the payer then carries it alone instead of dividing by zero.
    """
    return list(dict.fromkeys(expense["participants"])) or [expense["payer"]]


def split(amount_cents: int, participants: Sequence[int]) -> dict[int, int]:
    """Shares of *amount_cents* per participant, summing exactly to it.

    The cents that don't divide evenly go one each to the first participants,
    in the order stored, so the result is deterministic.
    """
    base, remainder = divmod(amount_cents, len(participants))
    return {p: base + (1 if i < remainder else 0) for i, p in enumerate(participants)}


def compute_balances(
    expenses: Iterable[Mapping[str, Any]], current_members: Sequence[int]
) -> dict[int, int]:
    """Net balance per person, in cents: positive is owed money, negative owes.

    Everyone who paid or took part in an expense gets a balance, members who
    left included, and so do the current members. The balances always sum to zero.
    """
    balances: dict[int, int] = {member: 0 for member in current_members}
    for expense in expenses:
        cents = to_cents(expense.get("amount", 0))
        payer = expense["payer"]
        balances[payer] = balances.get(payer, 0) + cents
        for person, share in split(cents, _participants(expense)).items():
            balances[person] = balances.get(person, 0) - share
    return balances


def visible_balances(balances: Mapping[int, int], current_members: Sequence[int]) -> dict[int, int]:
    """The balances to show: every current member, plus former members not yet settled."""
    members = set(current_members)
    return {p: b for p, b in balances.items() if p in members or b != 0}


def settlement_plan(balances: Mapping[int, int]) -> list[tuple[int, int, int]]:
    """Reimbursements that settle every balance: ``(from, to, cents)`` triples.

    Greedy: the largest debt pays the largest credit first, which settles a
    group in at most ``n - 1`` transfers. Ties break on the person's id, so the
    plan is stable from one ``/bilan`` to the next.
    """
    debtors = sorted(((-b, p) for p, b in balances.items() if b < 0), key=lambda x: (-x[0], x[1]))
    creditors = sorted(((b, p) for p, b in balances.items() if b > 0), key=lambda x: (-x[0], x[1]))
    plan: list[tuple[int, int, int]] = []
    d = c = 0
    while d < len(debtors) and c < len(creditors):
        owed, debtor = debtors[d]
        due, creditor = creditors[c]
        amount = min(owed, due)
        plan.append((debtor, creditor, amount))
        debtors[d] = (owed - amount, debtor)
        creditors[c] = (due - amount, creditor)
        if debtors[d][0] == 0:
            d += 1
        if creditors[c][0] == 0:
            c += 1
    return plan


def spending_total(expenses: Iterable[Mapping[str, Any]]) -> int:
    """What the group spent, in cents, reimbursements excluded."""
    return sum(to_cents(e.get("amount", 0)) for e in expenses if not is_transfer(e))


def format_amount(cents: int, currency: str, *, signed: bool = False) -> str:
    """``1234`` → ``"12.34€"`` (``"+12.34€"`` when *signed* and positive)."""
    sign = "-" if cents < 0 else ("+" if signed and cents > 0 else "")
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}{currency}"


__all__ = [
    "TRANSFER",
    "compute_balances",
    "format_amount",
    "is_transfer",
    "settlement_plan",
    "spending_total",
    "split",
    "to_cents",
    "visible_balances",
]
