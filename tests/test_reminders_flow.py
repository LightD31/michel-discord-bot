"""/reminder set and remove, and the minute check that sends them (T04)."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

from extensions.reminders import MAX_REMINDERS_PER_MEMBER, RemindersExtension
from features.reminders import Reminder
from src.core import clock


class MemoryReminders:
    """One guild's reminders, with the repository's claim semantics."""

    def __init__(self):
        self.items: dict[str, Reminder] = {}
        self.fail_claims = False

    async def add(self, reminder):
        rid = f"{len(self.items) + 1:024x}"
        self.items[rid] = reminder.model_copy(update={"id": rid})
        return rid

    async def delete(self, reminder_id):
        return 1 if self.items.pop(reminder_id, None) else 0

    async def count_for_user(self, user_id):
        return sum(r.user_id == user_id for r in self.items.values())

    async def list_for_user(self, user_id):
        return sorted(
            (r for r in self.items.values() if r.user_id == user_id), key=lambda r: r.remind_time
        )

    async def list_due(self, now):
        return [r for r in self.items.values() if r.remind_time <= now]

    async def claim_due(self, reminder, next_time):
        if self.fail_claims:
            return False
        stored = self.items.get(reminder.id)
        if stored is None or stored.remind_time != reminder.remind_time:
            return False
        if next_time is None:
            del self.items[reminder.id]
        else:
            stored.remind_time = next_time
        return True


@pytest.fixture
def setup(store_config):
    store_config({"servers": {"1": {"moduleUtils": {"enabled": True}}}})
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    member = guild.add_member(FakeMember(7, "bob"))
    repo = MemoryReminders()
    ctx = FakeCtx(member, channel)
    ext = bare_extension(RemindersExtension, ctx.bot, _reminder_repo=lambda guild_id: repo)
    return ext, ctx, repo, member


async def test_set_stores_a_reminder_at_the_paris_hour_typed(setup):
    ext, ctx, repo, _ = setup
    await run(
        RemindersExtension.reminder_set, ext, ctx, 20, 30, "Sortir le chien", frequency="daily"
    )
    (stored,) = repo.items.values()
    local = clock.to_local(stored.remind_time)
    assert (local.hour, local.minute) == (20, 30)
    assert stored.remind_time.utcoffset() == timedelta(0)
    assert stored.frequency == "daily" and stored.user_id == "7"
    assert "20:30" in ctx.last and ctx.replies[-1]["ephemeral"]


async def test_set_refuses_a_malformed_date(setup):
    ext, ctx, repo, _ = setup
    await run(RemindersExtension.reminder_set, ext, ctx, 9, 0, "x", date="2026-12-01")
    assert "JJ/MM/AAAA" in ctx.last and repo.items == {}


async def test_set_refuses_a_one_shot_in_the_past(setup):
    ext, ctx, repo, _ = setup
    await run(RemindersExtension.reminder_set, ext, ctx, 9, 0, "x", date="01/01/2020")
    assert "passé" in ctx.last and repo.items == {}


async def test_set_stops_at_the_quota(setup):
    ext, ctx, repo, _ = setup
    for _ in range(MAX_REMINDERS_PER_MEMBER):
        await run(RemindersExtension.reminder_set, ext, ctx, 9, 0, "x", frequency="daily")
    await run(RemindersExtension.reminder_set, ext, ctx, 9, 0, "one too many", frequency="daily")
    assert len(repo.items) == MAX_REMINDERS_PER_MEMBER
    assert "/reminder remove" in ctx.last


def due(repo, *, frequency=None, minutes_late=1, text="Rappel"):
    when = clock.now().replace(second=0, microsecond=0) - timedelta(minutes=minutes_late)
    rid = f"{len(repo.items) + 1:024x}"
    repo.items[rid] = Reminder(
        _id=rid, user_id="7", message=text, remind_time=when, frequency=frequency
    )
    return repo.items[rid]


async def test_a_due_one_shot_is_sent_once_then_gone(setup):
    ext, _, repo, member = setup
    due(repo, text="Appeler mamie")
    await RemindersExtension.check_reminders.callback(ext)
    await RemindersExtension.check_reminders.callback(ext)
    assert member.dms == ["Appeler mamie"]
    assert repo.items == {}


async def test_a_daily_reminder_moves_to_the_same_wall_time_tomorrow(setup):
    ext, _, repo, member = setup
    reminder = due(repo, frequency="daily")
    fired = reminder.remind_time
    await RemindersExtension.check_reminders.callback(ext)
    assert member.dms == ["Rappel"]
    (stored,) = repo.items.values()
    assert clock.to_local(stored.remind_time).time() == clock.to_local(fired).time()
    assert stored.remind_time > clock.now()


async def test_a_reminder_someone_else_claimed_is_not_sent(setup):
    ext, _, repo, member = setup
    due(repo)
    repo.fail_claims = True
    await RemindersExtension.check_reminders.callback(ext)
    assert member.dms == []


async def test_closed_dms_dont_stop_the_check(setup):
    ext, ctx, repo, member = setup
    member.dms_closed = True
    due(repo)
    other = ctx.guild.add_member(FakeMember(8, "eve"))
    repo.items["f" * 24] = Reminder(
        _id="f" * 24,
        user_id="8",
        message="Pour Eve",
        remind_time=clock.now() - timedelta(minutes=1),
    )
    await RemindersExtension.check_reminders.callback(ext)
    assert other.dms == ["Pour Eve"]


async def test_the_dm_carries_a_snooze_button_for_this_guild(setup, monkeypatch):
    ext, _, repo, member = setup
    sent = []

    async def send(content=None, **kwargs):
        sent.append(kwargs)

    member.send = send
    reminder = due(repo)
    await RemindersExtension.check_reminders.callback(ext)
    (button,) = sent[0]["components"][0].components
    assert button.custom_id == f"reminder_snooze:1:{reminder.id}"


async def test_remove_deletes_the_chosen_reminder(setup):
    ext, ctx, repo, member = setup
    await run(RemindersExtension.reminder_set, ext, ctx, 9, 0, "Garder", frequency="daily")
    await run(RemindersExtension.reminder_set, ext, ctx, 10, 0, "Supprimer", frequency="daily")
    target = next(r for r in repo.items.values() if r.message == "Supprimer")
    click = FakeCtx(member, ctx.channel, custom_id=target.id)

    async def wait_for_component(components, timeout):
        assert target.id in components
        return SimpleNamespace(ctx=click)

    ext.bot.wait_for_component = wait_for_component
    await run(RemindersExtension.delete_reminder, ext, ctx)
    assert [r.message for r in repo.items.values()] == ["Garder"]
    assert "10:00" in click.origin_edits[-1]["content"]


async def test_remove_without_reminders(setup):
    ext, ctx, _, _ = setup
    await run(RemindersExtension.delete_reminder, ext, ctx)
    assert "aucun rappel" in ctx.last


def test_a_reminder_set_on_a_date_reads_back_in_paris():
    # The stored instant of 1 July 09:00 Paris is 07:00 UTC (summer time).
    from features.reminders import first_occurrence

    now = datetime(2026, 6, 1, 12, 0, tzinfo=clock.LOCAL_TZ)
    when = first_occurrence(now, hour=9, minute=0, frequency=None, start=date(2026, 7, 1))
    assert (when.hour, clock.to_local(when).hour) == (7, 9)
