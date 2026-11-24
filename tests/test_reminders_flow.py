"""/rappel creer and supprimer, and the minute check that sends them (T04)."""

from datetime import timedelta

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

from extensions.reminders import MAX_REMINDERS_PER_MEMBER, RemindersExtension
from features.reminders import Reminder
from features.secretsanta.dates import WEEKDAYS
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

    async def delete_mine(self, user_id, reminder_ids):
        mine = [
            rid
            for rid in reminder_ids
            if self.items.get(rid, None) and self.items[rid].user_id == user_id
        ]
        for rid in mine:
            del self.items[rid]
        return len(mine)

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
    await run(RemindersExtension.reminder_set, ext, ctx, "Sortir le chien", "20h30", "daily")
    (stored,) = repo.items.values()
    local = clock.to_local(stored.remind_time)
    assert (local.hour, local.minute) == (20, 30)
    assert stored.remind_time.utcoffset() == timedelta(0)
    assert stored.frequency == "daily" and stored.user_id == "7"
    assert "Tous les jours à 20:30" in ctx.last and ctx.replies[-1]["ephemeral"]


async def test_set_reads_quand_like_the_message_form(setup):
    """« quand » is the same French reading as "⏰ Me le rappeler"."""
    ext, ctx, repo, _ = setup
    await run(RemindersExtension.reminder_set, ext, ctx, "Noël", "24/12 9h")
    (stored,) = repo.items.values()
    assert clock.to_local(stored.remind_time).strftime("%d/%m %H:%M") == "24/12 09:00"
    assert stored.frequency is None


async def test_set_refuses_what_it_cannot_read_with_examples(setup):
    ext, ctx, repo, _ = setup
    await run(RemindersExtension.reminder_set, ext, ctx, "x", "2026-12-01")
    assert ctx.last.startswith("📅 Je n'ai pas compris « 2026-12-01 ». Exemples :")
    assert repo.items == {}


async def test_set_refuses_a_moment_already_past(setup):
    ext, ctx, repo, _ = setup
    await run(RemindersExtension.reminder_set, ext, ctx, "x", "01/01/2020 9h")
    assert ctx.last.startswith("📅") and repo.items == {}


async def test_set_stops_at_the_quota(setup):
    ext, ctx, repo, _ = setup
    for _ in range(MAX_REMINDERS_PER_MEMBER):
        await run(RemindersExtension.reminder_set, ext, ctx, "x", "9h", "daily")
    await run(RemindersExtension.reminder_set, ext, ctx, "one too many", "9h", "daily")
    assert len(repo.items) == MAX_REMINDERS_PER_MEMBER
    assert "/rappel supprimer" in ctx.last


def test_quand_reads_back_as_it_is_typed():
    from extensions.reminders import when_choices

    (choice,) = when_choices("demain 18h30")
    assert choice["value"] == "demain 18h30"
    tomorrow = clock.to_local(clock.now()) + timedelta(days=1)
    assert choice["name"].startswith(f"→ {WEEKDAYS[tomorrow.weekday()]} ")
    assert choice["name"].endswith("à 18:30")
    (unread,) = when_choices("bientôt")
    assert unread["name"].startswith("❓ Je n'ai pas compris") and unread["value"] == "bientôt"
    examples = when_choices("")
    assert [c["value"] for c in examples] == ["dans 2h", "demain 18h30", "samedi 9h", "24/12 20h"]
    assert all(" → " in c["name"] and len(c["name"]) <= 100 for c in examples)


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


async def test_remove_offers_a_menu_and_deletes_what_was_chosen(setup):
    ext, ctx, repo, member = setup
    for task in ("Garder", "Supprimer", "Aussi"):
        await run(RemindersExtension.reminder_set, ext, ctx, task, "9h", "daily")
    await run(RemindersExtension.delete_reminder, ext, ctx)
    (row,) = ctx.replies[-1]["components"]
    (menu,) = row.components
    assert [o.label for o in menu.options] == ["Garder", "Supprimer", "Aussi"]
    assert menu.max_values == 3 and ctx.replies[-1]["ephemeral"]
    chosen = [o.value for o in menu.options if o.label != "Garder"]
    click = FakeCtx(member, ctx.channel, custom_id="reminder_delete")
    click.values = chosen
    await run(RemindersExtension.on_delete_chosen, ext, click)
    assert [r.message for r in repo.items.values()] == ["Garder"]
    assert click.origin_edits[-1]["content"] == "✅ 2 rappels supprimés."


async def test_remove_without_reminders(setup):
    ext, ctx, _, _ = setup
    await run(RemindersExtension.delete_reminder, ext, ctx)
    assert "aucun rappel" in ctx.last


# ── "⏰ Me le rappeler": a reminder made from a message ──────────────


async def test_a_message_becomes_a_reminder_that_links_back(setup):
    from discord_fakes import FakeMessage

    ext, ctx, repo, member = setup
    message = FakeMessage(500, content="Rendez-vous samedi", channel=ctx.channel)
    ctx.target = message
    await run(RemindersExtension.remind_me_menu, ext, ctx)
    (form,) = ctx.modals
    assert form.custom_id == "remind_msg:10:500"
    submit = FakeCtx(member, ctx.channel, custom_id=form.custom_id)
    await run(RemindersExtension.on_remind_form, ext, submit, when="dans 2h", note="Y aller")
    (stored,) = repo.items.values()
    assert stored.message == "Y aller" and stored.jump_url.endswith("/1/10/500")
    assert timedelta(minutes=119) < stored.remind_time - clock.now() <= timedelta(hours=2)
    assert "Je te le rappelle en MP <t:" in submit.last
    # The message's own text isn't copied: the link brings the member back to it.
    assert "Rendez-vous" not in stored.message

    stored.remind_time = clock.now() - timedelta(minutes=1)
    await RemindersExtension.check_reminders.callback(ext)
    assert member.dms[-1] == f"Y aller\n🔗 {stored.jump_url}"


async def test_a_when_it_cannot_read_says_how_to_write_it(setup):
    ext, ctx, repo, member = setup
    submit = FakeCtx(member, ctx.channel, custom_id="remind_msg:10:500")
    await run(RemindersExtension.on_remind_form, ext, submit, when="bientôt")
    assert submit.last.startswith("📅 Je n'ai pas compris « bientôt »")
    assert "demain 18h30" in submit.last and repo.items == {}
    await run(RemindersExtension.on_remind_form, ext, submit, when="demain")
    (stored,) = repo.items.values()
    assert stored.message == "Revoir ce message"
