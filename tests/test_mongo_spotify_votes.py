"""Spotify add-with-vote polls against a real MongoDB: one vote, one update (B06)."""

import asyncio

import pytest

from features.spotify.repository import SpotifyRepository

pytestmark = pytest.mark.mongo


async def test_withdrawing_a_vote_keeps_the_others(mongo):
    repo = SpotifyRepository(mongo.guild_id)
    assert await repo.open_addwithvote("t1", {"deadline": 1.0, "votes": {"1": "yes"}})
    assert await repo.open_addwithvote("t2", {"deadline": 1.0, "votes": {}})

    await asyncio.gather(
        repo.save_addwithvote_vote("1", None, "t1"),
        repo.save_addwithvote_vote("2", "no", "t1"),
        repo.save_addwithvote_vote("3", "yes", "t2"),
    )

    data = await repo.load_addwithvote_data()
    assert data["t1"]["votes"] == {"2": "no"}
    assert data["t2"]["votes"] == {"3": "yes"}


async def test_a_vote_on_a_closed_poll_doesnt_bring_it_back(mongo):
    repo = SpotifyRepository(mongo.guild_id)
    await repo.open_addwithvote("t1", {"deadline": 1.0, "votes": {}})
    await repo.delete_addwithvote("t1")

    assert await repo.save_addwithvote_vote("2", "yes", "t1") is None
    assert await repo.save_addwithvote_vote("2", None, "t1") is None
    assert await repo.load_addwithvote_data() == {}


async def test_a_poll_is_opened_once(mongo):
    repo = SpotifyRepository(mongo.guild_id)
    assert await repo.open_addwithvote("t1", {"deadline": 1.0, "votes": {"1": "yes"}})
    assert not await repo.open_addwithvote("t1", {"deadline": 9.0, "votes": {}})
    assert (await repo.get_addwithvote_doc("t1"))["deadline"] == 1.0


async def test_a_voter_id_must_be_a_discord_id(mongo):
    repo = SpotifyRepository(mongo.guild_id)
    with pytest.raises(ValueError):
        await repo.save_addwithvote_vote("$where", "yes", "t1")
