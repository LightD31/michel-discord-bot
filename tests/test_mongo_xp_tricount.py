"""XP and Tricount repositories against a real MongoDB (see tests/conftest.py).

XP's dashboard stats are server-side aggregations (``$setWindowFields``,
timezone-aware ``$dateToString``) that only a real server can check.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from features.tricount.repository import TricountRepository
from features.xp.repository import XpRepository

pytestmark = pytest.mark.mongo

SINCE = datetime(2026, 3, 1, tzinfo=UTC)


async def test_concurrent_awards_all_land(mongo):
    repo = XpRepository(mongo.guild_id)
    await repo.insert_new_user("1", 10, 0.0)
    await repo.insert_new_user("1", 999, 0.0)  # a racing first message: ignored
    await asyncio.gather(*(repo.award_xp("1", 5, 1, float(i)) for i in range(20)))
    user = await repo.get_user("1")
    assert (user["xp"], user["msg"]) == (10 + 20 * 5, 1 + 20)
    assert await repo.award_xp("nobody", 5, 1, 0.0) is None


async def test_ranks_follow_xp_with_ties_sharing_a_rank(mongo):
    repo = XpRepository(mongo.guild_id)
    for user, xp in (("a", 50), ("b", 300), ("c", 300), ("d", 10)):
        await repo.insert_new_user(user, xp, 0.0)
    assert [await repo.get_user_rank(u) for u in ("b", "c", "a", "d")] == [1, 1, 3, 4]
    assert await repo.get_user_rank("nobody") is None
    assert [u["_id"] for u in await repo.list_all_sorted_by_xp()][2:] == ["a", "d"]
    await repo.set_level("a", 4)
    assert (await repo.get_user("a"))["lvl"] == 4


async def log(repo, user, xp, ts, source="message"):
    await repo.log_event(user, xp, 0, ts, source)


async def test_daily_activity_is_bucketed_by_local_day(mongo):
    repo = XpRepository(mongo.guild_id)
    # 23:30 UTC on 9 March is already 10 March in Paris (UTC+1).
    await log(repo, "1", 10, datetime(2026, 3, 9, 23, 30, tzinfo=UTC))
    await log(repo, "1", 5, datetime(2026, 3, 10, 8, 0, tzinfo=UTC), "voice")
    await log(repo, "2", 7, datetime(2026, 3, 10, 9, 0, tzinfo=UTC))
    await log(repo, "2", 99, SINCE - timedelta(days=1))  # before the window
    rows = await repo.daily_activity(SINCE, "Europe/Paris")
    assert sorted((r["day"], r["source"], r["xp"], r["events"]) for r in rows) == [
        ("2026-03-10", "message", 17, 2),
        ("2026-03-10", "voice", 5, 1),
    ]
    utc_days = {r["day"] for r in await repo.daily_activity(SINCE, "UTC")}
    assert utc_days == {"2026-03-09", "2026-03-10"}


async def test_active_users_and_totals(mongo):
    repo = XpRepository(mongo.guild_id)
    await log(repo, "1", 10, datetime(2026, 3, 9, 10, 0, tzinfo=UTC))
    await log(repo, "1", 10, datetime(2026, 3, 9, 11, 0, tzinfo=UTC), "voice")
    await log(repo, "2", 3, datetime(2026, 3, 10, 10, 0, tzinfo=UTC))
    await log(repo, "3", 1, SINCE - timedelta(days=3))
    assert await repo.count_active_users(SINCE) == 2
    daily = {r["day"]: r["users"] for r in await repo.daily_active_users(SINCE, "UTC")}
    assert daily == {"2026-03-09": 1, "2026-03-10": 1}
    totals = {r["source"]: (r["xp"], r["events"]) for r in await repo.totals_by_source()}
    assert totals == {"message": (14, 3), "voice": (10, 1)}


async def test_the_heatmap_uses_local_weekday_and_hour(mongo):
    repo = XpRepository(mongo.guild_id)
    # Tuesday 10 March 2026, 23:30 UTC = Wednesday 00:30 in Paris.
    await log(repo, "1", 4, datetime(2026, 3, 10, 23, 30, tzinfo=UTC))
    (cell,) = await repo.activity_heatmap(SINCE, "Europe/Paris")
    assert (cell["dow"], cell["hour"], cell["xp"]) == (4, 0, 4)  # 1 = Sunday


async def test_the_xp_collection_is_created_once(mongo):
    repo = XpRepository(mongo.guild_id)
    assert await repo.ensure_collection() is True
    assert await repo.ensure_collection() is False


async def new_group(repo, name="coloc", members=(1, 2), active=True) -> ObjectId:
    return await repo.create_group({"name": name, "members": list(members), "is_active": active})


async def test_group_membership_and_lookup(mongo):
    repo = TricountRepository(mongo.guild_id)
    gid = await new_group(repo)
    await new_group(repo, "archive", members=(1,), active=False)
    await repo.add_group_member(gid, 3)
    await repo.remove_group_member(gid, 1)
    assert (await repo.find_active_group("coloc"))["members"] == [2, 3]
    assert await repo.find_active_group("archive") is None
    assert (await repo.find_group_by_name("archive"))["is_active"] is False
    assert await repo.find_active_group_by_id(gid) is not None
    assert [g["name"] for g in await repo.list_member_groups(3)] == ["coloc"]
    assert await repo.list_member_groups(1) == []  # only in the inactive one now


async def expense(repo, gid, day, *, payer=1, added_by=1, category="courses"):
    await repo.add_expense(
        {
            "group_id": gid,
            "payer": payer,
            "added_by": added_by,
            "amount": 10.0,
            "category": category,
            "date": datetime(2026, 3, day),
        }
    )


async def test_expenses_are_scoped_sorted_and_counted(mongo):
    repo = TricountRepository(mongo.guild_id)
    gid, other = await new_group(repo), await new_group(repo, "autre")
    for day in (3, 1, 2):
        await expense(repo, gid, day)
    await expense(repo, gid, 4, payer=2, added_by=2, category="loyer")
    await expense(repo, other, 5, payer=1)
    assert await repo.count_group_expenses(gid) == 4
    recent = await repo.list_recent_expenses(gid, 2)
    assert [e["date"].day for e in recent] == [4, 3]
    mine = await repo.list_user_expenses([gid, other], 1, limit=3)
    assert [e["date"].day for e in mine] == [5, 3, 2]
    assert sorted(await repo.distinct_categories()) == ["courses", "loyer"]


async def test_an_expense_id_from_another_group_or_malformed_is_not_found(mongo):
    repo = TricountRepository(mongo.guild_id)
    gid, other = await new_group(repo), await new_group(repo, "autre")
    await expense(repo, gid, 1)
    (stored,) = await repo.list_group_expenses(gid)
    assert await repo.find_expense_in_group(str(stored["_id"]), gid) is not None
    assert await repo.find_expense_in_group(stored["_id"], other) is None
    assert await repo.find_expense_in_group("not-an-id", gid) is None
    assert await repo.update_expense_fields(str(stored["_id"]), {"amount": 12.5}) is True
    assert await repo.update_expense_fields("not-an-id", {"amount": 1}) is False
    assert await repo.update_expense_fields(str(ObjectId()), {"amount": 1}) is False
    assert (await repo.find_expense_in_group(stored["_id"], gid))["amount"] == 12.5


async def test_recurring_expenses_are_due_stopped_and_rescheduled(mongo):
    repo = TricountRepository(mongo.guild_id)
    await repo.ensure_recurring_indexes()
    now = datetime(2026, 3, 10)
    due = await repo.add_recurring(
        {"active": True, "added_by": 1, "group_name": "coloc", "next_run": now}
    )
    later = await repo.add_recurring(
        {"active": True, "added_by": 1, "group_name": "autre", "next_run": now + timedelta(days=3)}
    )
    assert [r["_id"] for r in await repo.list_due_recurring(now)] == [due]
    assert [r["_id"] for r in await repo.list_active_recurring(1)] == [due, later]
    assert [r["_id"] for r in await repo.list_active_recurring(1, "autre")] == [later]

    await repo.reschedule_recurring(due, now + timedelta(days=30))
    assert await repo.list_due_recurring(now) == []

    assert await repo.stop_recurring(str(later), user_id=2) == 0  # not theirs
    assert await repo.stop_recurring("not-an-id", user_id=1) == 0
    assert await repo.stop_recurring(str(later), user_id=1) == 1
    assert await repo.stop_recurring(str(later), user_id=1) == 0  # already stopped
    await repo.deactivate_recurring(due)
    assert await repo.list_active_recurring(1) == []
