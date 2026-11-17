"""Moderation cases against a real MongoDB: case numbers, filters, revocation (T01)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pymongo.errors
import pytest

from features.moderation.models import Infraction
from features.moderation.repository import ModerationRepository
from src.core.errors import DatabaseError

pytestmark = pytest.mark.mongo

START = datetime(2026, 1, 1, tzinfo=UTC)


def case(repo_guild: str, case_id: int, *, user="7", type="warn", minutes=0, **extra):
    return Infraction(
        id=case_id,
        guild_id=repo_guild,
        user_id=user,
        moderator_id="1",
        type=type,
        created_at=START + timedelta(minutes=minutes),
        **extra,
    )


async def test_case_numbers_are_unique_under_concurrent_sanctions(mongo):
    repo = ModerationRepository(mongo.guild_id)
    ids = await asyncio.gather(*(repo.next_case_id() for _ in range(20)))
    assert sorted(ids) == list(range(1, 21))
    # Each guild counts on its own.
    assert await ModerationRepository(f"{mongo.guild_id}9").next_case_id() == 1
    await mongo.manager.client.drop_database(f"guild_{mongo.guild_id}9")


async def test_a_case_is_stored_and_read_back(mongo):
    repo = ModerationRepository(mongo.guild_id)
    await repo.ensure_indexes()
    stored = await repo.add(case(mongo.guild_id, 1, reason="spam", duration_seconds=60))
    assert stored.object_id
    got = await repo.get(1)
    assert got is not None
    assert (got.reason, got.duration_seconds, got.created_at) == ("spam", 60, START)
    assert await repo.get(2) is None


async def test_a_case_number_cannot_be_used_twice(mongo):
    repo = ModerationRepository(mongo.guild_id)
    await repo.ensure_indexes()
    await repo.add(case(mongo.guild_id, 1))
    with pytest.raises(DatabaseError):
        await repo.add(case(mongo.guild_id, 1, user="8"))


async def test_lists_are_newest_first_and_filtered(mongo):
    repo = ModerationRepository(mongo.guild_id)
    await repo.add(case(mongo.guild_id, 1, minutes=0))
    await repo.add(case(mongo.guild_id, 2, minutes=2, type="timeout"))
    await repo.add(case(mongo.guild_id, 3, minutes=1, user="8"))
    await repo.add(case(mongo.guild_id, 4, minutes=3, active=False))

    assert [c.id for c in await repo.list_for_user("7")] == [4, 2, 1]
    assert [c.id for c in await repo.list()] == [4, 2, 3, 1]
    assert [c.id for c in await repo.list(type="warn")] == [4, 3, 1]
    assert [c.id for c in await repo.list(user_id="7", active=True)] == [2, 1]
    assert [c.id for c in await repo.list(limit=2)] == [4, 2]


async def test_revoking_a_case_takes_it_out_of_the_warning_count(mongo):
    repo = ModerationRepository(mongo.guild_id)
    await repo.add(case(mongo.guild_id, 1))
    await repo.add(case(mongo.guild_id, 2))
    await repo.add(case(mongo.guild_id, 3, type="timeout"))
    assert await repo.count_active_warnings("7") == 2

    assert await repo.set_active(1, False) is True
    assert await repo.count_active_warnings("7") == 1
    assert (await repo.get(1)).active is False
    assert await repo.set_active(99, False) is False


async def test_an_outage_is_an_error_not_a_missing_case(mongo, monkeypatch):
    """``get`` used to answer None, which the revoke route showed as "Cas introuvable"."""
    repo = ModerationRepository(mongo.guild_id)

    class Down:
        async def find_one(self, *args, **kwargs):
            raise pymongo.errors.ServerSelectionTimeoutError("down")

        async def count_documents(self, *args, **kwargs):
            raise pymongo.errors.ServerSelectionTimeoutError("down")

    monkeypatch.setattr(repo, "_col", lambda: Down())
    with pytest.raises(DatabaseError):
        await repo.get(1)
    with pytest.raises(DatabaseError):
        await repo.count_active_warnings("7")
