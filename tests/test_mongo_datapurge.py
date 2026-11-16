"""A guild's data is deleted 30 days after the bot leaves it (O01)."""

from datetime import UTC, datetime, timedelta

import pytest

from features.datapurge import PURGE_DELAY, GuildPurgeRepository

pytestmark = pytest.mark.mongo

LEFT = datetime(2026, 10, 1, tzinfo=UTC)


async def test_a_purge_is_due_thirty_days_after_leaving(mongo):
    repo = GuildPurgeRepository()
    await repo.schedule(mongo.guild_id, LEFT)
    await repo.schedule(mongo.guild_id, LEFT + timedelta(days=10))  # the first date stands
    assert await repo.due(LEFT + PURGE_DELAY - timedelta(minutes=1)) == []
    assert await repo.due(LEFT + PURGE_DELAY) == [mongo.guild_id]


async def test_a_guild_seen_again_keeps_its_data(mongo):
    repo = GuildPurgeRepository()
    await repo.schedule(mongo.guild_id, LEFT)
    assert await repo.cancel([mongo.guild_id]) == 1
    assert await repo.due(LEFT + 2 * PURGE_DELAY) == []


async def test_purging_drops_the_guild_database(mongo):
    await mongo.guild_collection("xp").insert_one({"_id": "1", "xp": 10})
    assert mongo.guild_id in await mongo.manager.guild_ids_with_data()
    repo = GuildPurgeRepository()
    await repo.schedule(mongo.guild_id, LEFT)

    await repo.purge(mongo.guild_id)

    assert mongo.guild_id not in await mongo.manager.guild_ids_with_data()
    assert await repo.scheduled() == {}


async def test_departed_members_are_removed(mongo):
    from features.userinfo import UserInfoRepository

    repo = UserInfoRepository(mongo.guild_id)
    for uid in ("1", "2", "3"):
        await repo.upsert(uid, f"u{uid}", f"U{uid}")
    assert await repo.delete_absent(["1", "3"]) == 1
    assert sorted(m["id"] for m in await repo.all_members()) == ["1", "3"]
