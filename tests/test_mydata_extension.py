"""/mes-donnees and the purge of members who left, with in-memory stores."""

import json
from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

import extensions.mydata as mydata
from extensions.mydata import CONFIRM_ID, MyDataExtension
from features.userdata import Report


class Repo:
    instances: list["Repo"] = []

    def __init__(self, user_id):
        self.user_id = str(user_id)
        self.forgotten = None
        Repo.instances.append(self)

    async def export(self, guild_ids):
        return {
            "user_id": self.user_id,
            "guilds": {g: {"xp": [{"_id": self.user_id}]} for g in guild_ids},
        }

    async def forget(self, guild_ids, include_global=True):
        self.forgotten = (list(guild_ids), include_global)
        return Report(
            deleted={"guild_1.xp": 1}, anonymised={"guild_1.polls": 2}, kept=["guild_1.infractions"]
        )


class Departures:
    def __init__(self):
        self.calls = []

    async def schedule(self, guild_id, user_id, at):
        self.calls.append(("schedule", guild_id, user_id))

    async def cancel(self, guild_id, user_id):
        self.calls.append(("cancel", guild_id, user_id))
        return True

    async def due(self, now):
        return [("1", "5"), ("1", "6")]

    async def done(self, guild_id, user_id):
        self.calls.append(("done", guild_id, user_id))


@pytest.fixture
def setup(monkeypatch):
    Repo.instances = []
    departures = Departures()
    monkeypatch.setattr(mydata, "UserDataRepository", Repo)
    monkeypatch.setattr(mydata, "DeparturesRepository", lambda: departures)

    async def guild_ids():
        return ["1", "2"]

    monkeypatch.setattr(mydata.mongo_manager, "guild_ids_with_data", guild_ids)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    member = guild.add_member(FakeMember(5, "alice"))
    ctx = FakeCtx(member, channel)
    ext = bare_extension(MyDataExtension, ctx.bot)
    return ext, ctx, departures, guild


async def test_the_export_is_an_ephemeral_json_file(setup):
    ext, ctx, _, _ = setup
    await run(MyDataExtension.export, ext, ctx)
    reply = ctx.replies[-1]
    assert reply["ephemeral"] is True
    data = json.loads(reply["file"].file.getvalue())
    assert data["user_id"] == "5" and set(data["guilds"]) == {"1", "2"}


def press(ctx, custom_id):
    button = FakeCtx(ctx.author, ctx.channel, custom_id=custom_id)

    async def wait_for_component(**kwargs):
        return SimpleNamespace(ctx=button)

    ctx.bot.wait_for_component = wait_for_component
    return button


async def test_confirming_forgets_everywhere_and_says_what_was_kept(setup):
    ext, ctx, _, _ = setup
    button = press(ctx, CONFIRM_ID)
    await run(MyDataExtension.delete, ext, ctx)
    (repo,) = Repo.instances
    assert repo.forgotten == (["1", "2"], True)
    text = button.origin_edits[-1]["content"]
    assert "3 élément(s)" in text and "sanctions de modération" in text and "LLDAP" in text


async def test_cancelling_forgets_nothing(setup):
    ext, ctx, _, _ = setup
    button = press(ctx, "mydata_delete_cancel")
    await run(MyDataExtension.delete, ext, ctx)
    assert Repo.instances == [] and "rien n'a été effacé" in button.origin_edits[-1]["content"]


async def test_leaving_schedules_and_coming_back_cancels(setup):
    ext, _, departures, _ = setup
    member = SimpleNamespace(id=7, bot=False)
    await MyDataExtension.on_member_remove.callback(ext, SimpleNamespace(guild_id=1, member=member))
    await MyDataExtension.on_member_add.callback(ext, SimpleNamespace(guild_id=1, member=member))
    bot_member = SimpleNamespace(id=8, bot=True)
    await MyDataExtension.on_member_remove.callback(
        ext, SimpleNamespace(guild_id=1, member=bot_member)
    )
    assert departures.calls == [("schedule", "1", "7"), ("cancel", "1", "7")]


async def test_the_daily_task_forgets_on_that_server_only_members_still_gone(setup):
    ext, _, departures, guild = setup  # member 5 is back in the guild, 6 isn't
    await MyDataExtension.forget_departed_members.callback(ext)
    assert ("cancel", "1", "5") in departures.calls
    (repo,) = Repo.instances
    assert repo.user_id == "6" and repo.forgotten == (["1"], False)
    assert ("done", "1", "6") in departures.calls


def test_zunivers_reminders_forget_a_member_in_memory():
    from datetime import UTC, datetime

    from features.coloc.models import ReminderCollection, ReminderType

    reminders = ReminderCollection()
    at = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)
    reminders.add_reminder(at, "5", ReminderType.NORMAL)
    reminders.add_reminder(at, "6", ReminderType.HARDCORE)
    reminders.set_ninja_link("5", True)
    reminders.set_ninja_strategy("5", "fast")
    assert reminders.forget("5") == 3
    assert reminders.get_user_reminders("5") == [] and reminders.get_user_reminders("6")
    assert not reminders.wants_ninja_link("5") and reminders.ninja_strategy("5") is None
    assert reminders.forget("5") == 0


async def test_the_zunivers_hook_saves_what_it_removed():
    from datetime import UTC, datetime

    from extensions.zunivers import ZuniversExtension
    from features.coloc.models import ReminderCollection, ReminderType

    saved = []
    reminders = ReminderCollection()
    reminders.add_reminder(datetime(2026, 11, 17, tzinfo=UTC), "5", ReminderType.NORMAL)
    state = SimpleNamespace(
        reminders=reminders,
        request_subscriptions={"5": object()},
        storage=SimpleNamespace(save_reminders=lambda r: saved.append(r) or _done()),
    )
    ext = bare_extension(ZuniversExtension)

    async def get_state(guild_id):
        return state

    ext._state = get_state
    assert await ext.forget_member("1", "5") == 2
    assert saved == [reminders] and state.request_subscriptions == {}


async def _done():
    return None
