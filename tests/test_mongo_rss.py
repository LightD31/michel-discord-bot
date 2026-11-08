"""RSS feed bookkeeping against a real MongoDB (see tests/conftest.py)."""

import pytest

from features.rss.repository import MAX_SEEN_IDS, RssRepository

pytestmark = pytest.mark.mongo


async def test_a_poll_with_nothing_new_still_stamps_the_feed(mongo):
    repo = RssRepository(mongo.guild_id)
    await repo.initialize("news", ["e1"])
    before = (await repo.get("news")).last_poll_at

    await repo.record_poll("news", [])

    state = await repo.get("news")
    assert state.last_poll_at >= before
    assert state.seen_ids == ["e1"]


async def test_failures_add_up_and_a_success_clears_them(mongo):
    repo = RssRepository(mongo.guild_id)
    await repo.initialize("news", ["e1"])

    await repo.record_error("news", "HTTP 503")
    await repo.record_error("news", "HTTP 503")
    state = await repo.get("news")
    assert (state.failures, state.last_error) == (2, "HTTP 503")
    assert state.seen_ids == ["e1"]

    await repo.record_poll("news", ["e2"])
    state = await repo.get("news")
    assert (state.failures, state.last_error) == (0, None)
    assert state.seen_ids == ["e2", "e1"]


async def test_seen_ids_stay_capped_newest_first(mongo):
    repo = RssRepository(mongo.guild_id)
    await repo.initialize("news", [f"old{n}" for n in range(MAX_SEEN_IDS)])

    await repo.record_poll("news", ["new2", "new1"])

    seen = (await repo.get("news")).seen_ids
    assert len(seen) == MAX_SEEN_IDS
    assert seen[:3] == ["new2", "new1", "old0"]


async def test_every_state_of_the_guild_is_read_at_once(mongo):
    repo = RssRepository(mongo.guild_id)
    await repo.initialize("a", ["e1"])
    await repo.record_error("b", "DNS")

    states = await repo.states()

    assert set(states) == {"a", "b"}
    assert states["a"].initialized and not states["b"].initialized
    assert states["b"].failures == 1
    assert await repo.delete("b") == 1
    assert set(await repo.states()) == {"a"}
