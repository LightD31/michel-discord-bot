"""The member space's routes against a real MongoDB (C6): each one sees and changes the
session's own data only.

Every test seeds the same data for two members, ME and OTHER, then calls the
routes as ME: what they return never holds OTHER's data, and a write naming
OTHER's reminder, or a debt that isn't ME's, changes nothing.
"""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.webui.routes.me as me_routes
from features.birthday import BirthdayEntry, BirthdayRepository
from features.reminders import Reminder, ReminderRepository
from features.secretsanta import SecretSantaRepository, SecretSantaSession
from features.tricount.balances import TRANSFER, compute_balances
from features.tricount.repository import TricountRepository
from features.xp import XpRepository
from src.core import clock
from src.core.db import mongo_manager

pytestmark = pytest.mark.mongo

ME, OTHER = "7001", "7002"
MODULES = ("moduleXp", "moduleUtils", "moduleBirthday", "moduleTricount", "moduleSecretSanta")


class FakeCtx:
    """The member tier as the routes see it: ME, a member of the test guild only."""

    bot = None

    def __init__(self, gid: str) -> None:
        self.gid = gid
        self.session = SimpleNamespace(
            user_id=ME, username="moi", guilds=[{"id": gid, "name": "Les Copains"}]
        )

    def require_session(self, request):  # noqa: ARG002
        return self.session

    async def user_is_member(self, session, guild_id):  # noqa: ARG002
        return guild_id == self.gid

    async def require_guild_member(self, request, guild_id):  # noqa: ARG002
        if guild_id != self.gid:
            raise HTTPException(status_code=403, detail="Tu n'es pas membre de ce serveur.")
        return self.session


def endpoint(gid: str, method: str, path: str):
    router = me_routes.create_router(FakeCtx(gid))
    return next(r.endpoint for r in router.routes if r.path == path and method in r.methods)


def body_of(response) -> dict:
    return json.loads(response.body)


@pytest.fixture
def guild(mongo, store_config):
    gid = mongo.guild_id
    store_config({"servers": {gid: {m: {"enabled": True} for m in MODULES}}})
    return gid


async def seed(gid: str) -> dict:
    """The same kinds of data for ME and OTHER."""
    now = clock.now()
    reminders = ReminderRepository(gid)
    mine = await reminders.add(
        Reminder(user_id=ME, message="Sortir le chien", remind_time=now + timedelta(hours=2))
    )
    theirs = await reminders.add(
        Reminder(user_id=OTHER, message="secret d'OTHER", remind_time=now + timedelta(hours=3))
    )
    birthdays = BirthdayRepository(gid)
    await birthdays.upsert(
        BirthdayEntry(user=int(OTHER), date="02/02/1990", timezone="Europe/Paris")
    )
    xp = XpRepository(gid)
    await xp.insert_new_user(ME, 300, now.timestamp())
    await xp.insert_new_user(OTHER, 900, now.timestamp())
    return {"mine": mine, "theirs": theirs}


async def test_the_overview_holds_my_data_and_never_another_members(guild):
    ids = await seed(guild)
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["sections"] == ["xp", "reminders", "birthday", "tricount", "santa"]
    assert [r["id"] for r in out["reminders"]["items"]] == [ids["mine"]]
    assert out["birthday"] is None  # OTHER's birthday is not mine
    assert out["xp"]["xp"] == 300 and out["xp"]["rank"] == 2 and out["xp"]["ranked"] == 2
    assert "secret d'OTHER" not in json.dumps(out) and OTHER not in json.dumps(out)


async def test_a_module_off_is_neither_shown_nor_writable(guild, store_config):
    store_config({"servers": {guild: {"moduleXp": {"enabled": True}}}})
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["sections"] == ["xp"] and "reminders" not in out
    add = endpoint(guild, "POST", "/api/me/servers/{server_id}/reminders")
    with pytest.raises(HTTPException) as exc:
        await add(None, guild, me_routes.ReminderBody(message="x", when="dans 2h"))
    assert exc.value.status_code == 404
    assert await ReminderRepository(guild).list_for_user(ME) == []


async def test_reminders_are_created_changed_and_deleted_as_mine(guild):
    ids = await seed(guild)
    add = endpoint(guild, "POST", "/api/me/servers/{server_id}/reminders")
    created = body_of(
        await add(
            None,
            guild,
            me_routes.ReminderBody(message="Voter", when="demain 9h", frequency="daily"),
        )
    )
    repo = ReminderRepository(guild)
    mine = {r.id: r for r in await repo.list_for_user(ME)}
    assert created["id"] in mine and mine[created["id"]].frequency == "daily"
    assert clock.to_local(mine[created["id"]].remind_time).hour == 9

    edit = endpoint(guild, "PUT", "/api/me/servers/{server_id}/reminders/{reminder_id}")
    await edit(
        None,
        guild,
        ids["mine"],
        me_routes.ReminderBody(message="Promener le chien", when="dans 3h"),
    )
    assert (await repo.list_for_user(ME))[0].message == "Promener le chien"

    # OTHER's reminder, named by its id: refused, and left as it was.
    for call in (
        lambda: edit(
            None, guild, ids["theirs"], me_routes.ReminderBody(message="piraté", when="dans 1h")
        ),
        lambda: endpoint(guild, "DELETE", "/api/me/servers/{server_id}/reminders/{reminder_id}")(
            None, guild, ids["theirs"]
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404
    [theirs] = await repo.list_for_user(OTHER)
    assert theirs.message == "secret d'OTHER"

    delete = endpoint(guild, "DELETE", "/api/me/servers/{server_id}/reminders/{reminder_id}")
    await delete(None, guild, ids["mine"])
    assert [r.id for r in await repo.list_for_user(ME)] == [created["id"]]


async def test_an_unreadable_moment_says_why(guild):
    add = endpoint(guild, "POST", "/api/me/servers/{server_id}/reminders")
    with pytest.raises(HTTPException) as exc:
        await add(None, guild, me_routes.ReminderBody(message="x", when="un de ces jours"))
    assert exc.value.status_code == 422 and "Exemple" in exc.value.detail


async def test_the_reminder_limit_holds(guild, monkeypatch):
    monkeypatch.setattr(me_routes, "MAX_REMINDERS_PER_MEMBER", 1)
    add = endpoint(guild, "POST", "/api/me/servers/{server_id}/reminders")
    await add(None, guild, me_routes.ReminderBody(message="un", when="dans 2h"))
    with pytest.raises(HTTPException) as exc:
        await add(None, guild, me_routes.ReminderBody(message="deux", when="dans 2h"))
    assert exc.value.status_code == 409


async def test_my_birthday_is_set_and_removed_and_others_kept(guild):
    await seed(guild)
    put = endpoint(guild, "PUT", "/api/me/servers/{server_id}/birthday")
    await put(
        None, guild, me_routes.BirthdayBody(date="12 mars", timezone="Europe/Paris", hideYear=True)
    )
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["birthday"] == {
        "date": "12/03",
        "label": "12 mars",
        "hasYear": False,
        "hideYear": True,
        "timezone": "Europe/Paris",
    }
    with pytest.raises(HTTPException) as exc:
        await put(None, guild, me_routes.BirthdayBody(date="12/03/1990", timezone="Mars/Olympus"))
    assert exc.value.status_code == 422
    await endpoint(guild, "DELETE", "/api/me/servers/{server_id}/birthday")(None, guild)
    repo = BirthdayRepository(guild)
    assert await repo.find_one(int(ME)) is None
    assert await repo.find_one(int(OTHER)) is not None


async def test_a_new_date_on_the_day_is_not_wished_twice(guild):
    repo = BirthdayRepository(guild)
    await repo.upsert(BirthdayEntry(user=int(ME), date="12/03/1990", timezone="Europe/Paris"))
    assert await repo.mark_celebrated(int(ME))
    put = endpoint(guild, "PUT", "/api/me/servers/{server_id}/birthday")
    await put(None, guild, me_routes.BirthdayBody(date="12/03/1990", timezone="Europe/London"))
    entry = await repo.find_one(int(ME))
    assert entry.isBirthday is True and entry.timezone == "Europe/London"


async def tricount_group(gid: str) -> str:
    """ME, OTHER and a third member; OTHER paid 30 € for all three, the third paid 90 € for all."""
    repo = TricountRepository(gid)
    me, other, third = int(ME), int(OTHER), 7003
    group_id = await repo.create_group(
        {"name": "Vacances", "is_active": True, "members": [me, other, third], "created_by": me}
    )
    for payer, amount in ((other, 30), (third, 90)):
        await repo.add_expense(
            {
                "group_id": group_id,
                "amount": amount,
                "payer": payer,
                "participants": [me, other, third],
            }
        )
    return str(group_id)


async def test_tricount_shows_my_balance_and_my_lines_of_the_plan(guild):
    group_id = await tricount_group(guild)
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    [group] = out["tricount"]
    assert group["id"] == group_id and group["balance"] == -4000
    assert group["owes"] == [{"to": "7003", "cents": 4000}] and group["owed"] == []
    assert "7002" not in json.dumps(group)  # OTHER's balance is not mine to see


async def test_i_settle_my_own_debt_and_only_as_the_plan_says(guild):
    group_id = await tricount_group(guild)
    settle = endpoint(guild, "POST", "/api/me/servers/{server_id}/tricount/{group_id}/settle")
    # OTHER's debt to the third member is not mine to mark paid.
    for to, cents in (("7002", 4000), ("7003", 4001)):
        with pytest.raises(HTTPException) as exc:
            await settle(None, guild, group_id, me_routes.SettleBody(to=to, cents=cents))
        assert exc.value.status_code == 409
    await settle(None, guild, group_id, me_routes.SettleBody(to="7003", cents=4000))
    repo = TricountRepository(guild)
    group = await repo.find_active_group_by_str(group_id)
    expenses = await repo.list_group_expenses(group["_id"])
    [transfer] = [e for e in expenses if e.get("kind") == TRANSFER]
    assert (transfer["payer"], transfer["recipient"], transfer["added_by"]) == (7001, 7003, 7001)
    assert compute_balances(expenses, group["members"])[7001] == 0
    with pytest.raises(HTTPException) as exc:  # paid already: the plan no longer says so
        await settle(None, guild, group_id, me_routes.SettleBody(to="7003", cents=4000))
    assert exc.value.status_code == 409


async def test_a_part_of_my_debt_then_the_rest(guild):
    """P16: a repayment may be partial; the plan then shows what is left."""
    group_id = await tricount_group(guild)
    settle = endpoint(guild, "POST", "/api/me/servers/{server_id}/tricount/{group_id}/settle")
    await settle(None, guild, group_id, me_routes.SettleBody(to="7003", cents=1500))
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["tricount"][0]["owes"] == [{"to": "7003", "cents": 2500}]
    await settle(None, guild, group_id, me_routes.SettleBody(to="7003", cents=2500))
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["tricount"][0]["balance"] == 0


async def test_what_was_paid_back_to_me_only_lowers_a_debt_to_me(guild):
    """P16: the creditor records a repayment received; never more than the plan's line."""
    repo = TricountRepository(guild)
    me, other = int(ME), int(OTHER)
    oid = await repo.create_group({"name": "Coloc", "is_active": True, "members": [me, other]})
    group_id = str(oid)
    await repo.add_expense(
        {"group_id": oid, "amount": 50, "payer": me, "participants": [me, other]}
    )
    settle = endpoint(guild, "POST", "/api/me/servers/{server_id}/tricount/{group_id}/settle")
    for to, cents in (("7002", 2501), ("7003", 100)):
        with pytest.raises(HTTPException) as exc:
            body = me_routes.SettleBody(to=to, cents=cents, direction="received")
            await settle(None, guild, group_id, body)
        assert exc.value.status_code == 409
    body = me_routes.SettleBody(to="7002", cents=2500, direction="received")
    await settle(None, guild, group_id, body)
    group = await repo.find_active_group_by_str(group_id)
    expenses = await repo.list_group_expenses(group["_id"])
    [transfer] = [e for e in expenses if e.get("kind") == TRANSFER]
    assert (transfer["payer"], transfer["recipient"], transfer["added_by"]) == (other, me, me)
    assert compute_balances(expenses, group["members"]) == {me: 0, other: 0}


async def test_a_group_im_not_on_the_books_of_is_not_shown(guild):
    repo = TricountRepository(guild)
    await repo.create_group({"name": "Autres", "is_active": True, "members": [int(OTHER)]})
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["tricount"] == []
    settle = endpoint(guild, "POST", "/api/me/servers/{server_id}/tricount/{group_id}/settle")
    with pytest.raises(HTTPException) as exc:
        await settle(None, guild, "pas-un-id", me_routes.SettleBody(to="7003", cents=100))
    assert exc.value.status_code == 404


async def santa_session(gid: str, *, drawn: bool = False) -> SecretSantaRepository:
    repo = SecretSantaRepository()
    await repo.create_session(
        SecretSantaSession(
            context_id=f"guild_{gid}",
            channel_id=1,
            participants=[int(ME), int(OTHER)],
            is_drawn=drawn,
            exchange_date="2026-12-24",
        )
    )
    await repo.set_wishlist(
        f"guild_{gid}", int(OTHER), "la liste d'OTHER", clock.now() + timedelta(days=60)
    )
    return repo


async def test_my_wish_list_is_mine_alone(guild):
    repo = await santa_session(guild)
    put = endpoint(guild, "PUT", "/api/me/servers/{server_id}/santa/wishlist")
    await put(None, guild, me_routes.WishlistBody(text="  un livre  "))
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["santa"]["wishlist"] == "un livre" and out["santa"]["participant"] is True
    assert out["santa"]["exchangeLabel"] == "jeudi 24 décembre 2026"
    assert "la liste d'OTHER" not in json.dumps(out)
    assert (await repo.get_participant(f"guild_{guild}", int(OTHER))).wishlist == "la liste d'OTHER"


async def test_the_wish_list_closes_with_the_draw_and_needs_taking_part(guild):
    await santa_session(guild, drawn=True)
    put = endpoint(guild, "PUT", "/api/me/servers/{server_id}/santa/wishlist")
    with pytest.raises(HTTPException) as exc:
        await put(None, guild, me_routes.WishlistBody(text="trop tard"))
    assert exc.value.status_code == 409
    repo = SecretSantaRepository()
    await repo.create_session(
        SecretSantaSession(context_id=f"guild_{guild}", channel_id=1, participants=[int(OTHER)])
    )
    with pytest.raises(HTTPException) as exc:
        await put(None, guild, me_routes.WishlistBody(text="pas inscrit"))
    assert exc.value.status_code == 404


async def test_another_server_is_refused(guild):
    get = endpoint(guild, "GET", "/api/me/servers/{server_id}")
    with pytest.raises(HTTPException) as exc:
        await get(None, "123456")
    assert exc.value.status_code == 403


async def test_the_export_and_the_deletion_are_mine(guild, monkeypatch):
    await seed(guild)

    async def only_this_guild():
        return [guild]

    monkeypatch.setattr(mongo_manager, "guild_ids_with_data", only_this_guild)
    export = await endpoint(guild, "POST", "/api/me/data/export")(None)
    assert export.headers["content-disposition"] == 'attachment; filename="mes-donnees.json"'
    data = json.loads(export.body)
    assert "Sortir le chien" in json.dumps(data) and "secret d'OTHER" not in json.dumps(data)

    forget = endpoint(guild, "POST", "/api/me/data/delete")
    report = body_of(await forget(None, me_routes.DeleteBody(confirm=True)))
    assert report["deleted"] >= 2 and "Hors de portée" in report["notCovered"]
    assert await ReminderRepository(guild).list_for_user(ME) == []
    assert await XpRepository(guild).get_user(ME) is None
    assert len(await ReminderRepository(guild).list_for_user(OTHER)) == 1
    assert await XpRepository(guild).get_user(OTHER) is not None


async def test_a_former_member_who_still_owes_keeps_the_group(guild):
    group_id = await tricount_group(guild)
    repo = TricountRepository(guild)
    group = await repo.find_active_group_by_str(group_id)
    await repo.remove_group_member(group["_id"], int(ME))
    assert [g["name"] for g in await repo.groups_on_the_books(int(ME))] == ["Vacances"]
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["tricount"][0]["balance"] == -4000
    await repo.add_expense(
        {
            "group_id": group["_id"],
            "kind": TRANSFER,
            "amount": 40,
            "payer": int(ME),
            "recipient": 7003,
            "participants": [7003],
        }
    )
    out = body_of(await endpoint(guild, "GET", "/api/me/servers/{server_id}")(None, guild))
    assert out["tricount"] == []  # settled and gone: no longer on the books


async def test_i_join_and_leave_the_secret_santa_from_my_space(guild):
    """P16: as the "Participer" and "Se retirer" buttons; the wish list leaves with me."""
    repo = SecretSantaRepository()
    context = f"guild_{guild}"
    await repo.create_session(
        SecretSantaSession(context_id=context, channel_id=1, participants=[int(OTHER)])
    )
    join = endpoint(guild, "POST", "/api/me/servers/{server_id}/santa/join")
    leave = endpoint(guild, "POST", "/api/me/servers/{server_id}/santa/leave")
    await join(None, guild)
    await join(None, guild)  # twice: still once
    assert (await repo.get_session(context)).participants == [int(OTHER), int(ME)]
    put = endpoint(guild, "PUT", "/api/me/servers/{server_id}/santa/wishlist")
    await put(None, guild, me_routes.WishlistBody(text="un livre"))
    await leave(None, guild)
    assert (await repo.get_session(context)).participants == [int(OTHER)]
    assert (await repo.get_participant(context, int(ME))).wishlist is None

    await join(None, guild)
    await repo.claim_draw(context)
    for route in (join, leave):
        with pytest.raises(HTTPException) as exc:
            await route(None, guild)
        assert exc.value.status_code == 409
