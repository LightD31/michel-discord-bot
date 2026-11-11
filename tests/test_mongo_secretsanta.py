"""Secret Santa sessions against a real MongoDB: a session is drawn once."""

import asyncio

import pytest

from features.secretsanta import SecretSantaRepository, SecretSantaSession

pytestmark = pytest.mark.mongo


async def test_only_one_of_racing_draws_claims_the_session(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.save_session(
        SecretSantaSession(context_id=context_id, channel_id=1, participants=[1, 2, 3])
    )
    claims = await asyncio.gather(*(repo.claim_draw(context_id) for _ in range(4)))
    assert sorted(claims) == [False, False, False, True]
    session = await repo.get_session(context_id)
    assert session is not None and session.is_drawn and session.participants == [1, 2, 3]


async def test_a_missing_session_cannot_be_claimed(mongo):
    assert not await SecretSantaRepository().claim_draw(f"guild_{mongo.guild_id}")
