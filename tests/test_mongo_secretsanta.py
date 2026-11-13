"""Secret Santa sessions against a real MongoDB: a session is drawn once."""

import asyncio

import pytest

from features.secretsanta import SecretSantaRepository, SecretSantaSession

pytestmark = pytest.mark.mongo


async def test_only_one_of_racing_draws_claims_the_session(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(
        SecretSantaSession(context_id=context_id, channel_id=1, participants=[1, 2, 3])
    )
    claims = await asyncio.gather(*(repo.claim_draw(context_id) for _ in range(4)))
    assert sorted(claims) == [False, False, False, True]
    session = await repo.get_session(context_id)
    assert session is not None and session.is_drawn and session.participants == [1, 2, 3]


async def test_a_missing_session_cannot_be_claimed(mongo):
    assert not await SecretSantaRepository().claim_draw(f"guild_{mongo.guild_id}")


async def test_concurrent_joins_are_all_kept(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(SecretSantaSession(context_id=context_id, channel_id=1))
    joined = await asyncio.gather(*(repo.add_participant(context_id, uid) for uid in range(10)))
    assert all(s is not None for s in joined)
    session = await repo.get_session(context_id)
    assert sorted(session.participants) == list(range(10))
    assert await repo.add_participant(context_id, 3) is None  # already in


async def test_a_join_after_the_draw_changes_nothing(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(
        SecretSantaSession(context_id=context_id, channel_id=1, participants=[1, 2])
    )
    assert await repo.claim_draw(context_id)
    assert await repo.add_participant(context_id, 3) is None
    assert await repo.remove_participant(context_id, 1) is None
    session = await repo.get_session(context_id)
    assert session.is_drawn and session.participants == [1, 2]


async def test_a_member_leaves_an_open_session(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(
        SecretSantaSession(context_id=context_id, channel_id=1, participants=[1, 2])
    )
    assert (await repo.remove_participant(context_id, 1)).participants == [2]
    assert await repo.remove_participant(context_id, 1) is None


async def test_create_replaces_a_drawn_session(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(SecretSantaSession(context_id=context_id, channel_id=1))
    await repo.claim_draw(context_id)
    await repo.create_session(SecretSantaSession(context_id=context_id, channel_id=2))
    session = await repo.get_session(context_id)
    assert not session.is_drawn and session.channel_id == 2
