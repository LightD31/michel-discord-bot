"""Giveaway entries, draws and cancellations against a real MongoDB: one winner per race."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from features.giveaway import Giveaway, GiveawayRepository

pytestmark = pytest.mark.mongo

NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


async def add(repo: GiveawayRepository, message_id="1", ends_at=NOW - timedelta(minutes=1)) -> str:
    return await repo.add(
        Giveaway(
            guild_id="1",
            channel_id="2",
            message_id=message_id,
            host_id="3",
            prize="Un jeu",
            ends_at=ends_at,
        )
    )


async def test_only_one_of_two_racing_draws_claims_the_giveaway(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    gid = await add(repo)
    claims = await asyncio.gather(*(repo.claim_draw(gid) for _ in range(5)))
    assert sum(claim is not None for claim in claims) == 1
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


async def test_racing_clicks_enter_each_member_once(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    await add(repo, ends_at=NOW + timedelta(hours=1))
    clicks = [repo.add_entrant("1", uid, NOW) for uid in ("7", "8", "7", "9", "8")]
    befores = await asyncio.gather(*clicks)
    stored = await repo.get_by_message("1")
    assert stored is not None and sorted(stored.entrants) == ["7", "8", "9"]
    # Each "before" tells a new entry from a repeated click: two repeats here.
    repeated = [
        uid for uid, b in zip(("7", "8", "7", "9", "8"), befores, strict=True) if uid in b.entrants
    ]
    assert sorted(repeated) == ["7", "8"]


async def test_a_member_withdraws_and_the_claim_returns_the_final_entrants(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    gid = await add(repo, ends_at=NOW + timedelta(hours=1))
    await repo.add_entrant("1", "7", NOW)
    await repo.add_entrant("1", "8", NOW)
    before = await repo.remove_entrant("1", "7", NOW)
    assert before is not None and before.entrants == ["7", "8"]
    claimed = await repo.claim_draw(gid)
    assert claimed is not None and claimed.drawn and claimed.entrants == ["8"]
    # Entries close with the draw.
    assert await repo.add_entrant("1", "9", NOW) is None


async def test_no_entry_after_the_end_or_a_cancellation(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    await add(repo, message_id="1")  # ended a minute ago
    cancelled = await add(repo, message_id="2", ends_at=NOW + timedelta(hours=1))
    assert await repo.mark_cancelled(cancelled)
    assert await repo.add_entrant("1", "7", NOW) is None
    assert await repo.add_entrant("2", "7", NOW) is None
    assert await repo.remove_entrant("2", "7", NOW) is None


async def test_rerolls_offer_the_latest_drawn_giveaways(mongo):
    repo = GiveawayRepository(mongo.guild_id)
    for i, hours in enumerate((3, 1, 2)):
        gid = await add(repo, message_id=str(i), ends_at=NOW - timedelta(hours=hours))
        assert await repo.claim_draw(gid)
    await add(repo, message_id="open", ends_at=NOW + timedelta(hours=1))
    latest = await repo.list_drawn(2)
    assert [g.message_id for g in latest] == ["1", "2"]
