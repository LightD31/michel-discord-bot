"""Secret Santa sessions against a real MongoDB: a session is drawn once."""

import asyncio
from datetime import UTC, datetime, timedelta

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


# ── Participants' own entries (P2) ─────────────────────────────────────────

EXPIRES = datetime(2027, 1, 23, tzinfo=UTC)


async def _participants(mongo):
    return mongo.manager.get_guild_db(mongo.guild_id)["secret_santa_participants"]


async def test_a_wish_list_is_stored_changed_and_cleared(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.set_wishlist(context_id, 7, "un livre", EXPIRES)
    await repo.set_wishlist(context_id, 8, "du thé", EXPIRES)
    await repo.set_wishlist(context_id, 7, "deux livres", EXPIRES)
    assert await repo.wishlists(context_id) == {7: "deux livres", 8: "du thé"}
    await repo.set_wishlist(context_id, 8, None, EXPIRES)
    await repo.clear_wishlist(context_id, 7)
    assert await repo.wishlists(context_id) == {}
    doc = await (await _participants(mongo)).find_one({"_id": f"{context_id}:7"})
    assert doc["user_id"] == 7 and doc["context_id"] == context_id
    assert doc["expires_at"].replace(tzinfo=UTC) == EXPIRES


async def test_the_entries_expire_through_a_ttl_index(mongo):
    repo = SecretSantaRepository()
    await repo.set_wishlist(f"guild_{mongo.guild_id}", 7, "un livre", EXPIRES)
    indexes = await (await _participants(mongo)).index_information()
    assert indexes["expires_ttl"]["expireAfterSeconds"] == 0
    assert "user_id_idx" in indexes


async def test_a_gift_is_marked_once_even_when_clicked_twice_at_once(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.set_wishlist(context_id, 7, "un livre", EXPIRES)
    marks = await asyncio.gather(
        *(repo.mark_gift(context_id, 7, "sent_at", EXPIRES) for _ in range(4))
    )
    assert sorted(marks) == [False, False, False, True]
    assert await repo.mark_gift(context_id, 8, "received_at", EXPIRES)
    assert await repo.gift_marks(context_id) == ({7}, {8})
    person = await repo.get_participant(context_id, 7)
    assert person.wishlist == "un livre" and person.sent_at and person.received_at is None
    with pytest.raises(ValueError):
        await repo.mark_gift(context_id, 7, "wishlist", EXPIRES)


async def test_cancelling_or_replacing_a_session_drops_its_entries(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(SecretSantaSession(context_id=context_id, channel_id=1))
    await repo.set_wishlist(context_id, 7, "un livre", EXPIRES)
    await repo.create_session(SecretSantaSession(context_id=context_id, channel_id=2))
    assert await repo.wishlists(context_id) == {}
    await repo.set_wishlist(context_id, 7, "un livre", EXPIRES)
    await repo.mark_gift(context_id, 8, "sent_at", EXPIRES)
    assert await repo.delete_session(context_id)
    assert await (await _participants(mongo)).count_documents({}) == 0


async def test_changing_the_date_moves_the_expiry_and_rearms_the_reminder(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(
        SecretSantaSession(context_id=context_id, channel_id=1, participants=[7, 8], shipping=True)
    )
    await repo.set_wishlist(context_id, 7, "un livre", EXPIRES)
    await repo.claim_draw(context_id)
    assert await repo.claim_reminder(context_id)
    assert not await repo.claim_reminder(context_id)  # once

    later = EXPIRES + timedelta(days=7)
    session = await repo.update_settings(context_id, {"exchange_date": "2026-12-31"}, later)
    assert session.exchange_date == "2026-12-31" and not session.reminded
    doc = await (await _participants(mongo)).find_one({"_id": f"{context_id}:7"})
    assert doc["expires_at"].replace(tzinfo=UTC) == later
    assert (await repo.update_settings(context_id, {"budget": "30 €"}, later)).budget == "30 €"
    assert await repo.update_settings(f"guild_0{mongo.guild_id}", {"budget": "x"}, later) is None


async def test_only_a_drawn_parcel_session_has_a_reminder(mongo):
    repo = SecretSantaRepository()
    context_id = f"guild_{mongo.guild_id}"
    await repo.create_session(SecretSantaSession(context_id=context_id, channel_id=1))
    await repo.claim_draw(context_id)
    assert not await repo.claim_reminder(context_id)  # an in-person exchange
