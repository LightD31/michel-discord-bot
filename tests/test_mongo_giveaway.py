"""Giveaway draws and cancellations against a real MongoDB: one winner per race."""

import asyncio
from datetime import datetime, timedelta

import pytest

from features.giveaway import Giveaway, GiveawayRepository

pytestmark = pytest.mark.mongo

NOW = datetime(2026, 3, 10, 12, 0)


async def add(repo: GiveawayRepository, message_id="1") -> str:
    return await repo.add(
        Giveaway(
            guild_id="1",
            channel_id="2",
            message_id=message_id,
            host_id="3",
            prize="Un jeu",
            ends_at=NOW - timedelta(minutes=1),
        )
    )


async def test_only_one_of_two_racing_draws_claims_the_giveaway(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    gid = await add(repo)
    claims = await asyncio.gather(*(repo.claim_draw(gid) for _ in range(5)))
    assert sorted(claims) == [False, False, False, False, True]
    assert await repo.list_due(NOW) == []


async def test_a_released_draw_comes_back_with_its_failure_counted(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    gid = await add(repo)
    assert await repo.claim_draw(gid)
    await repo.release_draw(gid)
    [due] = await repo.list_due(NOW)
    assert due.id == gid and due.draw_failures == 1 and not due.drawn
    assert await repo.claim_draw(gid)


async def test_a_cancelled_giveaway_cannot_be_drawn_and_a_drawn_one_cannot_be_cancelled(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    cancelled = await add(repo, message_id="1")
    drawn = await add(repo, message_id="2")
    assert await repo.mark_cancelled(cancelled)
    assert not await repo.mark_cancelled(cancelled)
    assert not await repo.claim_draw(cancelled)
    assert await repo.claim_draw(drawn)
    assert not await repo.mark_cancelled(drawn)


async def test_winners_are_saved_after_the_claim(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    gid = await add(repo)
    assert await repo.claim_draw(gid)
    await repo.update_winners(gid, ["7", "8"])
    stored = await repo.get_by_message("1")
    assert stored is not None and stored.drawn and stored.winners == ["7", "8"]
