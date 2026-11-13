"""YouTube and poll repositories against a real MongoDB (see tests/conftest.py)."""

import asyncio
from datetime import datetime, timedelta

import pytest

from features.polls.models import Poll
from features.polls.repository import PollRepository
from features.youtube import YoutubeRepository
from src.core.errors import DatabaseError

pytestmark = pytest.mark.mongo

NOW = datetime(2026, 3, 10, 12, 0)


async def test_a_guild_never_synced_has_no_last_videos(mongo):
    assert await YoutubeRepository(mongo.guild_id).get_last_video_ids() is None


async def test_handles_with_dots_round_trip(mongo):
    # "$set": {"@john.doe": …} would store {"@john": {"doe": …}} and the video
    # would never be recognised again, then re-posted on every poll.
    repo = YoutubeRepository(mongo.guild_id)
    ids = {"@john.doe": "v1", "@alpha": "v2", "plain": "v3"}
    await repo.save_last_video_ids(ids)
    assert await repo.get_last_video_ids() == ids


async def test_saving_replaces_the_map(mongo):
    repo = YoutubeRepository(mongo.guild_id)
    await repo.save_last_video_ids({"@alpha": "v1", "@gone": "v0"})
    await repo.save_last_video_ids({"@alpha": "v2"})
    assert await repo.get_last_video_ids() == {"@alpha": "v2"}
    await repo.save_last_video_ids({})
    assert await repo.get_last_video_ids() == {}


def poll(message_id="m1", closes_in=None, author_id="2", **kw) -> Poll:
    closes_at = NOW + timedelta(minutes=closes_in) if closes_in is not None else None
    return Poll(
        channel_id="1",
        message_id=message_id,
        author_id=author_id,
        question="?",
        options=["a", "b", "c"],
        closes_at=closes_at,
        **kw,
    )


async def test_votes_are_set_replaced_and_cleared(mongo):
    repo = PollRepository(mongo.guild_id)
    pid = await repo.add(poll())
    await repo.set_vote(pid, "10", [0])
    await repo.set_vote(pid, "11", [2, 0, 1])
    await repo.set_vote(pid, "10", [1])
    assert (await repo.get_by_message("m1")).votes == {"10": [1], "11": [2, 0, 1]}
    await repo.clear_vote(pid, "11")
    assert (await repo.get_by_message("m1")).votes == {"10": [1]}
    assert await repo.get_by_message("unknown") is None


async def test_only_open_polls_past_their_deadline_are_due(mongo):
    repo = PollRepository(mongo.guild_id)
    due = await repo.add(poll("due", closes_in=-1))
    await repo.add(poll("later", closes_in=10))
    await repo.add(poll("open-ended"))
    closed = await repo.add(poll("closed", closes_in=-5))
    await repo.mark_closed(closed)
    assert [p.id for p in await repo.list_due(NOW)] == [due]


async def test_editing_a_poll_replaces_its_options(mongo):
    repo = PollRepository(mongo.guild_id)
    pid = await repo.add(poll(mode="reaction"))
    await repo.set_options(pid, ["x", "y"], ["1️⃣", "2️⃣"])
    stored = await repo.get_by_message("m1")
    assert (stored.options, stored.emojis, stored.mode) == (["x", "y"], ["1️⃣", "2️⃣"], "reaction")


async def test_one_poll_per_message(mongo):
    repo = PollRepository(mongo.guild_id)
    await repo.ensure_indexes()
    await repo.add(poll())
    with pytest.raises(DatabaseError):
        await repo.add(poll())


async def test_editpoll_offers_the_authors_open_reaction_polls_newest_first(mongo):
    repo = PollRepository(mongo.guild_id)
    await repo.add(poll("old", mode="reaction", author_id="7"))
    await repo.add(poll("buttons", mode="public", author_id="7"))
    await repo.add(poll("someone", mode="reaction", author_id="8"))
    closed_id = await repo.add(poll("closed", mode="reaction", author_id="7"))
    await repo.mark_closed(closed_id)
    await repo.add(poll("new", mode="reaction", author_id="7"))
    found = await repo.list_open_reaction_polls("7", 25)
    assert [p.message_id for p in found] == ["new", "old"]
    assert [p.message_id for p in await repo.list_open_reaction_polls("7", 1)] == ["new"]


async def test_quick_clicks_on_a_ranked_poll_keep_every_rank(mongo):
    repo = PollRepository(mongo.guild_id)
    pid = await repo.add(poll(mode="ranked"))
    await asyncio.gather(*(repo.add_to_ranking(pid, "10", option) for option in (2, 0, 1)))
    ranking = (await repo.get_by_message("m1")).votes["10"]
    assert sorted(ranking) == [0, 1, 2]
    again = await repo.add_to_ranking(pid, "10", 0)
    assert again.votes["10"] == ranking  # an option already ranked isn't added twice


async def test_a_closed_poll_takes_no_vote(mongo):
    repo = PollRepository(mongo.guild_id)
    pid = await repo.add(poll(mode="ranked"))
    await repo.set_vote(pid, "10", [1])
    await repo.mark_closed(pid)
    assert await repo.add_to_ranking(pid, "10", 0) is None
    assert await repo.set_vote(pid, "11", [0]) is None
    assert await repo.clear_vote(pid, "10") is None
    assert (await repo.get_by_message("m1")).votes == {"10": [1]}
