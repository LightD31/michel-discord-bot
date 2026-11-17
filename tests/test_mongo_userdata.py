"""/mes-donnees against a real MongoDB: what is deleted, anonymised and kept (F10)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from features.userdata import (
    NOBODY,
    DeparturesRepository,
    UserDataRepository,
    export_json,
    register_forget_hook,
    unregister_forget_hook,
    user_id_of,
)
from src.core import db

pytestmark = pytest.mark.mongo

ME = "111111111111111111"
OTHER = "222222222222222222"


async def seed(mongo):
    g = mongo.guild_collection
    await g("xp").insert_many([{"_id": ME, "xp": 10}, {"_id": OTHER, "xp": 5}])
    await g("xp_events").insert_many([{"user_id": ME}, {"user_id": ME}, {"user_id": OTHER}])
    await g("xp_monthly").insert_one({"_id": {"month": "2025-01", "user_id": ME}, "xp": 3})
    await g("birthday").insert_one({"user": int(ME), "date": "x"})
    await g("task_reminders").insert_one({"user_id": ME, "message": "m"})
    await g("feur_stats").insert_many([{"_id": f"user_{ME}", "total": 2}, {"_id": "guild_total"}])
    await g("users").insert_one({"_id": ME, "username": "me"})
    await g("polls").insert_one({"_id": "p", "author_id": ME, "votes": {ME: [1], OTHER: [0]}})
    await g("suggestions").insert_one({"_id": "s", "author_id": OTHER, "votes": {ME: "up"}})
    await g("playlistItemsFull").insert_one({"_id": "t", "added_by": ME})
    await g("ai_comparisons").insert_one({"_id": "c", "asker_id": ME, "votes": {ME: "gpt"}})
    await g("giveaways").insert_one({"_id": "gw", "winners": [OTHER, ME]})
    await g("confrerie_drafts").insert_one({"_id": "d", "submitter_id": int(ME)})
    await g("infractions").insert_one({"id": 1, "user_id": ME, "type": "warn"})
    await g("secret_santa_sessions").insert_many(
        [
            {"_id": "open", "participants": [int(ME), int(OTHER)], "is_drawn": False},
            {"_id": "drawn", "participants": [int(ME), int(OTHER)], "is_drawn": True},
        ]
    )
    await g("secret_santa_draw_results").insert_one(
        {"_id": "drawn", "results": [[int(ME), int(OTHER)], [int(OTHER), int(ME)]]}
    )
    await g("secret_santa_banned_pairs").insert_one({"_id": "open", "pairs": [[int(ME), 3]]})
    await g("secret_santa_participants").insert_many(
        [
            {"_id": f"drawn:{ME}", "context_id": "drawn", "user_id": int(ME), "wishlist": "thé"},
            {
                "_id": f"drawn:{OTHER}",
                "context_id": "drawn",
                "user_id": int(OTHER),
                "wishlist": "x",
            },
        ]
    )
    await db.mongo_manager.get_global_collection("secret_santa_participants").insert_one(
        {"_id": f"channel_9:{ME}", "context_id": "channel_9", "user_id": int(ME), "wishlist": "dm"}
    )
    await db.mongo_manager.get_global_collection("ai_usage").insert_many(
        [{"_id": f"2026-11-17:user:{ME}", "calls": 3}, {"_id": "2026-11-17:all", "calls": 9}]
    )


async def test_the_export_holds_the_member_and_nobody_else(mongo):
    await seed(mongo)
    data = await UserDataRepository(ME).export([mongo.guild_id])
    mine = data["guilds"][mongo.guild_id]
    assert [d["_id"] for d in mine["xp"]] == [ME]
    assert len(mine["xp_events"]) == 2
    polls = mine["polls"]
    assert [p.get("votes") for p in polls] == [None, {ME: [1]}]  # never the others' votes
    assert mine["infractions"][0]["type"] == "warn"
    (draw,) = mine["secret_santa_draw_results"]
    assert all(int(ME) in pair for pair in draw["results"])
    assert data["global"]["ai_usage"][0]["calls"] == 3
    # Their own wish list, never another participant's.
    assert [d["wishlist"] for d in mine["secret_santa_participants"]] == ["thé"]
    assert [d["wishlist"] for d in data["global"]["secret_santa_participants"]] == ["dm"]
    exported = json.loads(export_json(data))
    assert exported["user_id"] == ME


async def test_forgetting_deletes_anonymises_and_keeps(mongo):
    await seed(mongo)
    report = await UserDataRepository(ME).forget([mongo.guild_id])
    g = mongo.guild_collection

    assert await g("xp").find_one({"_id": ME}) is None
    assert await g("xp").find_one({"_id": OTHER}) is not None
    assert await g("xp_events").count_documents({}) == 1
    for name in ("xp_monthly", "birthday", "task_reminders", "users"):
        assert await g(name).count_documents({}) == 0, name
    assert await g("feur_stats").count_documents({}) == 1  # the guild total stays

    poll = await g("polls").find_one({"_id": "p"})
    assert poll["author_id"] == NOBODY and ME not in poll["votes"] and OTHER in poll["votes"]
    assert len(poll["votes"]) == 2  # their vote still counts, under a key naming nobody
    assert all(k.startswith("0") for k in poll["votes"] if k != OTHER)
    assert ME not in (await g("suggestions").find_one())["votes"]
    assert (await g("playlistItemsFull").find_one())["added_by"] == NOBODY
    assert (await g("ai_comparisons").find_one())["asker_id"] == NOBODY
    assert (await g("giveaways").find_one())["winners"] == [OTHER, NOBODY]
    assert (await g("confrerie_drafts").find_one())["submitter_id"] == 0

    assert await g("infractions").count_documents({"user_id": ME}) == 1
    sessions = {s["_id"]: s for s in await g("secret_santa_sessions").find().to_list()}
    assert sessions["open"]["participants"] == [int(OTHER)]
    assert int(ME) in sessions["drawn"]["participants"]  # the draw is made
    assert (await g("secret_santa_banned_pairs").find_one())["pairs"] == []
    kept = {where.rsplit(".", 1)[1] for where in report.kept}
    assert kept == {"infractions", "secret_santa_draw_results"}
    entries = await g("secret_santa_participants").find().to_list()
    assert [e["user_id"] for e in entries] == [int(OTHER)]
    global_entries = db.mongo_manager.get_global_collection("secret_santa_participants")
    assert await global_entries.count_documents({}) == 0

    usage = await db.mongo_manager.get_global_collection("ai_usage").find().to_list()
    assert [u["_id"] for u in usage] == ["2026-11-17:all"]
    assert report.total > 10


async def test_tricount_is_kept_while_a_balance_is_open_then_anonymised(mongo):
    g = mongo.guild_collection
    me, other = int(ME), int(OTHER)
    await g("tricount_groups").insert_one({"_id": "grp", "members": [me, other], "is_active": True})
    await g("tricount_expenses").insert_one(
        {"group_id": "grp", "amount": 10, "payer": me, "participants": [me, other], "added_by": me}
    )
    report = await UserDataRepository(ME).forget([mongo.guild_id])
    assert any(w.endswith(".tricount") for w in report.kept)
    assert (await g("tricount_groups").find_one())["members"] == [me, other]

    # Settled up: OTHER paid ME back.
    await g("tricount_expenses").insert_one(
        {"group_id": "grp", "amount": 5, "payer": other, "participants": [me], "kind": "transfer"}
    )
    report = await UserDataRepository(ME).forget([mongo.guild_id])
    assert not report.kept
    assert (await g("tricount_groups").find_one())["members"] == [other]
    expenses = await g("tricount_expenses").find().to_list()
    assert all(me not in e["participants"] and e["payer"] != me for e in expenses)


async def test_hooks_run_for_each_guild(mongo):
    calls = []

    async def hook(guild_id, user_id):
        calls.append((guild_id, user_id))
        return 2

    register_forget_hook("test", hook)
    try:
        report = await UserDataRepository(ME).forget([mongo.guild_id], include_global=False)
    finally:
        unregister_forget_hook("test")
    assert calls == [(mongo.guild_id, ME)]
    assert report.deleted[f"guild_{mongo.guild_id}.test"] == 2


def test_only_a_real_discord_id_reaches_a_field_path():
    for bad in ("1.2", "$x", "0123", "", "abc"):
        with pytest.raises(ValueError):
            user_id_of(bad)
    assert user_id_of(111) == "111"


async def test_departures_are_due_30_days_later_unless_cancelled(mongo):
    repo = DeparturesRepository()
    await repo.ensure_indexes()
    left = datetime(2026, 1, 1, tzinfo=UTC)
    await repo.schedule("1", ME, left)
    await repo.schedule("1", ME, left + timedelta(days=10))  # the first departure wins
    await repo.schedule("1", OTHER, left)
    await repo.schedule("2", ME, left)
    assert await repo.due(left + timedelta(days=29)) == []
    assert set(await repo.due(left + timedelta(days=30))) == {("1", ME), ("1", OTHER), ("2", ME)}
    assert await repo.cancel("1", OTHER) is True
    assert await repo.cancel_guild("2") == 1
    await repo.done("1", ME)
    assert await repo.due(left + timedelta(days=99)) == []
