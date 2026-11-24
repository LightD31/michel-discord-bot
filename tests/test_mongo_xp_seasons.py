"""XP seasons against a real MongoDB: the ranking of a period, a podium kept once."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from features.userdata import NOBODY, UserDataRepository
from features.xp import XpRepository

pytestmark = pytest.mark.mongo

START = datetime(2026, 11, 1, tzinfo=UTC)
END = datetime(2026, 12, 1, tzinfo=UTC)
ME = "111111111111111111"
OTHER = "222222222222222222"


async def test_the_ranking_counts_only_the_season(mongo):
    repo = XpRepository(mongo.guild_id)
    await repo.ensure_indexes()
    for user, xp, ts in [
        (ME, 20, START - timedelta(seconds=1)),  # the season before
        (ME, 15, START),
        (ME, 10, START + timedelta(days=3)),
        (OTHER, 30, START + timedelta(days=10)),
        (OTHER, 50, END),  # the next one
    ]:
        await repo.log_event(user, xp, 0, ts)
    assert await repo.season_ranking(START, END) == [(OTHER, 30), (ME, 25)]
    assert await repo.season_ranking(START, END, limit=1) == [(OTHER, 30)]
    assert await repo.season_xp(ME, START, END) == 25
    assert await repo.season_xp("333", START, END) == 0


async def test_a_season_is_recorded_once_and_its_members_are_forgotten(mongo):
    repo = XpRepository(mongo.guild_id)
    top = [(ME, 25), (OTHER, 10)]
    results = await asyncio.gather(
        *(repo.record_season("2026-11", "novembre 2026", top, END) for _ in range(5))
    )
    assert results.count(True) == 1
    stored = await repo.get_season("2026-11")
    assert stored["top"][0] == {"user_id": ME, "xp": 25}

    data = await UserDataRepository(ME).export([mongo.guild_id])
    (mine,) = data["guilds"][mongo.guild_id]["xp_seasons"]
    assert mine["top"] == [{"user_id": ME, "xp": 25}]  # their line, not the others'

    await UserDataRepository(ME).forget([mongo.guild_id])
    stored = await repo.get_season("2026-11")
    assert [e["user_id"] for e in stored["top"]] == [NOBODY, OTHER]
