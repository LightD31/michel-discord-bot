"""/reminder list and the snooze button (F03)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import extensions.reminders as reminders_module
from extensions.reminders import MAX_REMINDERS_PER_MEMBER, RemindersExtension
from features.reminders import Reminder


class FakeRepo:
    def __init__(self, reminders=(), count=None):
        self.reminders = list(reminders)
        self.count = len(self.reminders) if count is None else count
        self.added = []

    async def list_for_user(self, user_id):
        return [r for r in self.reminders if r.user_id == user_id]

    async def count_for_user(self, user_id):
        return self.count

    async def add(self, reminder):
        self.added.append(reminder)
        return "ffff"


class FakeCtx:
    def __init__(self, content=None):
        self.guild_id = 1
        self.author = SimpleNamespace(id=7)
        self.custom_id = "reminder_snooze:1:abcdef"
        self.message = SimpleNamespace(content=content)
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class FakeExt:
    """What the callbacks use of the extension: the per-guild repository."""

    def __init__(self):
        self.repos = {}

    def _reminder_repo(self, guild_id):
        return self.repos[str(guild_id)]


@pytest.fixture
def ext(monkeypatch):
    monkeypatch.setattr(reminders_module, "_snooze_minutes", lambda gid: 10)
    return FakeExt()


def with_repo(ext, repo):
    ext.repos["1"] = repo
    return repo


def reminder(text, frequency=None):
    return Reminder(
        _id="a" * 24,
        user_id="7",
        message=text,
        remind_time=datetime.now() + timedelta(hours=1),
        frequency=frequency,
    )


async def test_list_shows_each_reminder_and_its_schedule(ext):
    with_repo(ext, FakeRepo([reminder("Sortir le chien", "daily"), reminder("x" * 500)]))
    ctx = FakeCtx()
    await RemindersExtension.list_reminders.callback(ext, ctx)
    [(_, kwargs)] = ctx.sent
    assert kwargs["ephemeral"] is True
    [embed] = kwargs["embeds"]
    assert "Sortir le chien" in embed.description and "Tous les jours" in embed.description
    assert "<t:" in embed.description
    assert "x" * 200 not in embed.description  # long texts are previewed
    assert f"2/{MAX_REMINDERS_PER_MEMBER}" in embed.title


async def test_list_without_reminders_says_so(ext):
    with_repo(ext, FakeRepo())
    ctx = FakeCtx()
    await RemindersExtension.list_reminders.callback(ext, ctx)
    assert "aucun rappel" in ctx.sent[0][0]


async def test_snoozing_a_snoozed_reminder_keeps_one_mark(ext):
    repo = with_repo(ext, FakeRepo())
    await RemindersExtension.on_snooze.callback(ext, FakeCtx("⏰ (snooze) Sortir le chien"))
    [snoozed] = repo.added
    assert snoozed.message == "⏰ (snooze) Sortir le chien"
    assert snoozed.user_id == "7" and snoozed.frequency is None


async def test_snoozes_count_toward_the_quota(ext):
    repo = with_repo(ext, FakeRepo(count=MAX_REMINDERS_PER_MEMBER))
    ctx = FakeCtx("Sortir le chien")
    await RemindersExtension.on_snooze.callback(ext, ctx)
    assert repo.added == []
    assert str(MAX_REMINDERS_PER_MEMBER) in ctx.sent[0][0]
