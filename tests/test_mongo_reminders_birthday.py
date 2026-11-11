"""Reminder and birthday repositories against a real MongoDB (see tests/conftest.py)."""

from datetime import datetime, timedelta

import pytest

from features.birthday import BirthdayEntry, BirthdayRepository
from features.reminders import Reminder, ReminderRepository
from features.reminders.repository import COLLECTION as REMINDERS
from features.reminders.repository import TTL_GRACE_SECONDS

pytestmark = pytest.mark.mongo

NOW = datetime(2026, 3, 10, 12, 0)


def reminder(user="1", minutes=0, **kw) -> Reminder:
    return Reminder(user_id=user, message="m", remind_time=NOW + timedelta(minutes=minutes), **kw)


async def test_reminders_are_listed_per_user_soonest_first(mongo):
    repo = ReminderRepository(mongo.guild_id)
    later = await repo.add(reminder(minutes=30))
    sooner = await repo.add(reminder(minutes=5, frequency="daily"))
    await repo.add(reminder(user="9"))
    listed = await repo.list_for_user("1")
    assert [r.id for r in listed] == [sooner, later]
    assert listed[0].frequency == "daily"
    assert await repo.count_for_user("1") == 2


async def test_only_due_reminders_are_returned(mongo):
    repo = ReminderRepository(mongo.guild_id)
    due = await repo.add(reminder(minutes=-1))
    on_time = await repo.add(reminder(minutes=0))
    await repo.add(reminder(minutes=1))
    assert {r.id for r in await repo.list_due(NOW)} == {due, on_time}


async def test_a_due_reminder_is_claimed_once(mongo):
    repo = ReminderRepository(mongo.guild_id)
    await repo.add(reminder(minutes=-1, frequency="daily"))
    [due] = await repo.list_due(NOW)
    claims = [await repo.claim_due(due, NOW + timedelta(days=1)) for _ in range(3)]
    assert claims == [True, False, False]  # a second check holding the same copy sends nothing
    assert await repo.list_due(NOW) == []


async def test_a_one_shot_reminder_is_claimed_by_deleting_it(mongo):
    repo = ReminderRepository(mongo.guild_id)
    await repo.add(reminder(minutes=-1))
    [due] = await repo.list_due(NOW)
    assert await repo.claim_due(due, None)
    assert not await repo.claim_due(due, None)
    assert await repo.list_for_user("1") == []


async def test_a_reminder_is_rescheduled_then_deleted(mongo):
    repo = ReminderRepository(mongo.guild_id)
    rid = await repo.add(reminder(minutes=-5, frequency="weekly"))
    await repo.reschedule(rid, NOW + timedelta(weeks=1))
    assert await repo.list_due(NOW) == []
    assert await repo.delete(rid) == 1
    assert await repo.delete(rid) == 0
    assert await repo.list_for_user("1") == []


async def test_fired_reminders_expire_through_a_ttl_index(mongo):
    repo = ReminderRepository(mongo.guild_id)
    await repo.ensure_indexes()
    indexes = await mongo.guild_collection(REMINDERS).index_information()
    assert indexes["remind_time_ttl"]["expireAfterSeconds"] == TTL_GRACE_SECONDS
    await repo.ensure_indexes()  # idempotent on restart


def birthday(user=1, day="14/07/1990", **kw) -> BirthdayEntry:
    return BirthdayEntry(user=user, date=day, timezone="Europe/Paris", **kw)


async def test_a_birthday_is_saved_once_per_member(mongo):
    repo = BirthdayRepository(mongo.guild_id)
    await repo.ensure_indexes()
    await repo.upsert(birthday())
    await repo.upsert(birthday(day="15/07/1990", hideyear=True))
    assert await mongo.guild_collection("birthday").count_documents({}) == 1
    saved = await repo.find_one(1)
    assert saved == birthday(day="15/07/1990", hideyear=True)
    assert await repo.find_one(2) is None


async def test_the_celebration_flag_is_updated_in_place(mongo):
    repo = BirthdayRepository(mongo.guild_id)
    await repo.upsert(birthday())
    await repo.update_fields(1, {"isBirthday": True})
    assert (await repo.find_one(1)).isBirthday


async def test_a_malformed_document_does_not_hide_the_others(mongo):
    repo = BirthdayRepository(mongo.guild_id)
    await repo.upsert(birthday(user=1))
    await repo.upsert(birthday(user=2))
    await mongo.guild_collection("birthday").insert_one({"user": 3, "timezone": "Nowhere"})
    assert sorted(e.user for e in await repo.find_all()) == [1, 2]


async def test_a_birthday_is_deleted(mongo):
    repo = BirthdayRepository(mongo.guild_id)
    await repo.upsert(birthday())
    assert await repo.delete(1) == 1
    assert await repo.delete(1) == 0
