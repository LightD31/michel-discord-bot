"""Tricount entries can't outlive their author's goodwill (S04).

Leaving a group stops the recurrences one pays, and the group's creator (or a
server manager) can fix or remove anyone's expense.
"""

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run
from interactions import Permissions

import extensions.tricount.expenses as expenses_module
import extensions.tricount.groups as groups_module
from extensions.tricount import TricountExtension


class MemoryTricount:
    def __init__(self):
        self.group = {"_id": "G", "name": "Coloc", "members": [3, 4], "creator": 4}
        self.expenses = {
            "e1": {
                "_id": "e1",
                "group_id": "G",
                "payer": 3,
                "added_by": 3,
                "amount": 1e6,
                "description": "Farce",
            }
        }
        self.stopped_for: list[int] = []

    async def find_active_group(self, name):
        return self.group if name == self.group["name"] else None

    async def remove_group_member(self, group_id, user_id):
        self.group["members"].remove(user_id)

    async def stop_member_recurring(self, group_id, user_id):
        self.stopped_for.append(user_id)
        return 1

    async def list_group_expenses(self, group_id):
        return []

    async def find_expense_in_group(self, expense_id, group_id):
        return self.expenses.get(expense_id)

    async def delete_expense(self, expense_id, group_id):
        return self.expenses.pop(expense_id, None) is not None

    async def update_expense_fields(self, expense_id, fields):
        self.expenses[expense_id].update(fields)
        return True


@pytest.fixture
def setup(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleTricount": {"enabled": True}}}})
    repo = MemoryTricount()
    for module in (groups_module, expenses_module):
        monkeypatch.setattr(module, "TricountRepository", lambda guild_id: repo)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))

    def ctx_for(uid, permissions=Permissions.NONE):
        ctx = FakeCtx(guild.add_member(FakeMember(uid, permissions=permissions)), channel)
        return bare_extension(TricountExtension, ctx.bot), ctx

    return ctx_for, repo


async def test_leaving_stops_the_members_recurrences(setup):
    ctx_for, repo = setup
    ext, ctx = ctx_for(3)
    await run(TricountExtension.tricount_quitter, ext, ctx, "Coloc")
    assert repo.stopped_for == [3] and repo.group["members"] == [4]
    assert "récurrente(s) arrêtée(s)" in ctx.replies[-1]["embed"].description


async def test_a_member_cannot_delete_someone_elses_expense(setup):
    ctx_for, repo = setup
    repo.group["members"].append(5)
    ext, ctx = ctx_for(5)
    await run(TricountExtension.supprimer_depense, ext, ctx, "Coloc", "e1")
    assert "e1" in repo.expenses and ctx.last.startswith("❌")


@pytest.mark.parametrize(
    ("uid", "permissions"), [(4, Permissions.NONE), (6, Permissions.MANAGE_GUILD)]
)
async def test_the_creator_or_a_manager_deletes_any_expense(setup, uid, permissions):
    ctx_for, repo = setup
    ext, ctx = ctx_for(uid, permissions)
    await run(TricountExtension.supprimer_depense, ext, ctx, "Coloc", "e1")
    assert repo.expenses == {}
    assert ctx.replies[-1]["embed"].title == "🗑️ Dépense supprimée"


async def test_the_author_may_still_delete_their_own(setup):
    ctx_for, repo = setup
    ext, ctx = ctx_for(3)
    await run(TricountExtension.supprimer_depense, ext, ctx, "Coloc", "e1")
    assert repo.expenses == {}


async def test_the_creator_fixes_an_expense_left_by_a_departed_member(setup):
    ctx_for, repo = setup
    repo.group["members"].remove(3)
    ext, ctx = ctx_for(4)
    await run(TricountExtension.modifier_depense, ext, ctx, "Coloc", "e1", nouveau_montant=10.0)
    assert repo.expenses["e1"]["amount"] == 10.0
