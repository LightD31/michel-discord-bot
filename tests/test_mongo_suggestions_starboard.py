"""Suggestion and starboard repositories against a real MongoDB (see tests/conftest.py)."""

import asyncio
from datetime import datetime

import pytest

from features.starboard import StarboardRepository, StarEntry
from features.suggestions import Suggestion, SuggestionsRepository
from src.core.errors import DatabaseError

pytestmark = pytest.mark.mongo


async def new_suggestion(repo, text="idée", **kw) -> Suggestion:
    sugg_id = await repo.next_id()
    return await repo.add(
        Suggestion(
            id=sugg_id,
            guild_id="1",
            channel_id="2",
            author_id="3",
            text=text,
            created_at=datetime(2026, 1, sugg_id),
            **kw,
        )
    )


async def test_concurrent_suggestions_get_distinct_ids(mongo):
    repo = SuggestionsRepository(mongo.guild_id)
    ids = await asyncio.gather(*(repo.next_id() for _ in range(20)))
    assert sorted(ids) == list(range(1, 21))


async def test_a_vote_is_cast_switched_and_withdrawn(mongo):
    repo = SuggestionsRepository(mongo.guild_id)
    s = await new_suggestion(repo)
    assert (await repo.toggle_vote(s.id, "10", "up")).votes == {"10": "up"}
    assert (await repo.toggle_vote(s.id, "11", "down")).tally() == (1, 1)
    assert (await repo.toggle_vote(s.id, "10", "down")).votes == {"10": "down", "11": "down"}
    assert (await repo.toggle_vote(s.id, "10", "down")).votes == {"11": "down"}


async def test_a_double_click_withdraws_the_vote(mongo):
    # Both clicks land: the second sees the first's vote and removes it.
    repo = SuggestionsRepository(mongo.guild_id)
    s = await new_suggestion(repo)
    await asyncio.gather(repo.toggle_vote(s.id, "10", "up"), repo.toggle_vote(s.id, "10", "up"))
    assert (await repo.get(s.id)).votes == {}


async def test_a_decided_suggestion_takes_no_more_votes(mongo):
    repo = SuggestionsRepository(mongo.guild_id)
    s = await new_suggestion(repo)
    await repo.toggle_vote(s.id, "10", "up")
    await repo.decide(s.id, "approved", "bonne idée", "99", from_statuses=("pending",))
    assert await repo.toggle_vote(s.id, "11", "up") is None
    assert await repo.toggle_vote(404, "11", "up") is None
    decided = await repo.get(s.id)
    assert (decided.status, decided.reason, decided.decided_by) == ("approved", "bonne idée", "99")
    assert decided.votes == {"10": "up"}


async def test_a_vote_needs_a_discord_user_id(mongo):
    repo = SuggestionsRepository(mongo.guild_id)
    s = await new_suggestion(repo)
    with pytest.raises(ValueError):
        await repo.toggle_vote(s.id, "$where", "up")


async def test_suggestions_are_listed_newest_first_and_by_status(mongo):
    repo = SuggestionsRepository(mongo.guild_id)
    first = await new_suggestion(repo, "a")
    second = await new_suggestion(repo, "b")
    await repo.decide(first.id, "denied", None, "99", from_statuses=("pending",))
    assert [s.id for s in await repo.list()] == [second.id, first.id]
    assert [s.id for s in await repo.list("pending")] == [second.id]
    await repo.set_message(second.id, "555")
    assert (await repo.get(second.id)).message_id == "555"


async def test_two_moderators_deciding_at_once_decide_once(mongo):
    """Two clicks on ✅ / ❌ at the same moment: one decision, the other told it's taken."""
    repo = SuggestionsRepository(mongo.guild_id)
    s = await new_suggestion(repo)
    results = await asyncio.gather(
        repo.decide(s.id, "approved", "oui", "1", from_statuses=("pending",)),
        repo.decide(s.id, "denied", "non", "2", from_statuses=("pending",)),
    )
    decided = [r for r in results if r is not None]
    assert len(decided) == 1
    assert (await repo.get(s.id)).status == decided[0].status
    # An approved idea can still be marked implemented, a refused one can't.
    implemented = await repo.decide(
        s.id, "implemented", None, "1", from_statuses=("pending", "approved")
    )
    assert (implemented is not None) == (decided[0].status == "approved")


async def test_open_suggestions_are_listed_for_the_staff(mongo):
    repo = SuggestionsRepository(mongo.guild_id)
    first = await new_suggestion(repo, "a")
    second = await new_suggestion(repo, "b")
    third = await new_suggestion(repo, "c")
    await repo.decide(first.id, "approved", None, "9", from_statuses=("pending",))
    await repo.decide(second.id, "denied", None, "9", from_statuses=("pending",))
    assert [x.id for x in await repo.list_in(["pending"], 25)] == [third.id]
    assert [x.id for x in await repo.list_in(["pending", "approved"], 25)] == [third.id, first.id]
    assert len(await repo.list_in(["pending", "approved"], 1)) == 1
    await repo.set_thread(third.id, "777")
    assert (await repo.get(third.id)).thread_id == "777"


async def test_the_human_id_is_unique(mongo):
    repo = SuggestionsRepository(mongo.guild_id)
    await repo.ensure_indexes()
    s = await new_suggestion(repo)
    with pytest.raises(DatabaseError):
        await repo.add(s.model_copy(update={"object_id": None}))


def star(message_id="100", count=3) -> StarEntry:
    return StarEntry(
        guild_id="1",
        channel_id="2",
        original_message_id=message_id,
        mirror_channel_id="3",
        mirror_message_id="900",
        author_id="4",
        count=count,
        created_at=datetime(2026, 1, 1),
    )


async def test_a_mirror_is_recorded_once_and_its_count_kept(mongo):
    repo = StarboardRepository(mongo.guild_id)
    await repo.ensure_indexes()
    assert await repo.get_by_original("100") is None
    await repo.upsert(star())
    await repo.upsert(star(count=4))
    assert await mongo.guild_collection("starboard").count_documents({}) == 1
    await repo.update_count("100", 7)
    entry = await repo.get_by_original("100")
    assert (entry.id, entry.count, entry.mirror_message_id) == ("100", 7, "900")


async def test_a_mirror_is_deleted(mongo):
    repo = StarboardRepository(mongo.guild_id)
    await repo.upsert(star())
    assert await repo.delete_by_original("100") == 1
    assert await repo.delete_by_original("100") == 0
    assert await repo.get_by_original("100") is None
