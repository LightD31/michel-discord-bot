"""A birthday is marked celebrated once the wish is sent, not before (B13)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from discord_fakes import FakeBot, FakeChannel, FakeGuild, FakeMember, bare_extension

from extensions.birthday import BirthdayExtension
from features.birthday import NO_YEAR, BirthdayEntry, parse_birth_date

GUILD = 1
NOW = datetime(2026, 7, 14, 10, tzinfo=ZoneInfo("Europe/Paris"))


class Repo:
    def __init__(self):
        self.celebrated: set[int] = set()

    async def mark_celebrated(self, user_id):
        if user_id in self.celebrated:
            return False
        self.celebrated.add(user_id)
        return True


class DownChannel(FakeChannel):
    async def send(self, content=None, **kwargs):
        raise TimeoutError("Discord didn't answer")


@pytest.fixture
def setup(store_config):
    store_config(
        {"servers": {str(GUILD): {"moduleBirthday": {"enabled": True, "birthdayChannelId": "10"}}}}
    )
    guild = FakeGuild(GUILD)
    guild.add_member(FakeMember(7, "bob"))
    repo = Repo()
    ext = bare_extension(BirthdayExtension, FakeBot(guild), _repo=lambda guild_id: repo)
    return ext, guild, repo


def entry():
    return BirthdayEntry(user=7, date="14/07/1990", timezone="Europe/Paris")


async def test_the_wish_is_sent_then_the_day_marked(setup):
    ext, guild, repo = setup
    channel = guild.add_channel(FakeChannel(10))
    await ext._handle_birthday_celebration(entry(), GUILD, NOW)
    assert len(channel.sent) == 1 and repo.celebrated == {7}


async def test_a_discord_outage_leaves_the_day_to_retry(setup):
    ext, guild, repo = setup
    guild.add_channel(DownChannel(10))
    with pytest.raises(TimeoutError):
        await ext._handle_birthday_celebration(entry(), GUILD, NOW)
    assert repo.celebrated == set()  # the next hourly run tries again

    channel = guild.add_channel(FakeChannel(10))
    await ext._handle_birthday_celebration(entry(), GUILD, NOW)
    assert len(channel.sent) == 1 and repo.celebrated == {7}


async def test_a_member_who_left_is_not_retried_all_day(setup):
    ext, guild, repo = setup
    guild.members.pop(7)
    await ext._handle_birthday_celebration(entry(), GUILD, NOW)
    assert repo.celebrated == {7}


async def test_a_birthday_without_its_year_is_wished_without_an_age(setup, store_config):
    """A wish that says the age is skipped: "12/03" gave none (B2)."""
    ext, guild, repo = setup
    channel = guild.add_channel(FakeChannel(10))
    store_config(
        {
            "servers": {
                str(GUILD): {
                    "moduleBirthday": {
                        "enabled": True,
                        "birthdayChannelId": "10",
                        "birthdayMessageList": [
                            {"text": "{mention} a {age} ans !", "weight": 100},
                            {"text": "Bon anniversaire {mention} !", "weight": 1},
                        ],
                    }
                }
            }
        }
    )
    dated = BirthdayEntry(
        user=7, date=parse_birth_date("14/07", today=NOW.date()), timezone="Europe/Paris"
    )
    await ext._handle_birthday_celebration(dated, GUILD, NOW)
    assert channel.sent[-1].content == "Bon anniversaire <@7> !"


async def test_the_command_takes_paris_and_a_day_without_its_year(store_config):
    from discord_fakes import FakeCtx, run

    store_config({"servers": {str(GUILD): {"moduleBirthday": {"enabled": True}}}})
    guild = FakeGuild(GUILD)
    channel = guild.add_channel(FakeChannel(10))
    ctx = FakeCtx(guild.add_member(FakeMember(7, "bob")), channel)
    saved = []

    class Store:
        async def find_one(self, user):
            return None

        async def upsert(self, entry):
            saved.append(entry)

    ext = bare_extension(BirthdayExtension, ctx.bot, _repo=lambda guild_id: Store())
    await run(BirthdayExtension.anniversaire, ext, ctx, "1er mars")
    (stored,) = saved
    assert stored.timezone == "Europe/Paris" and stored.date.year == NO_YEAR
    assert ctx.last == "✅ Anniversaire ajouté : 1er mars."
    await run(BirthdayExtension.anniversaire, ext, ctx, "31/02")
    assert ctx.last.startswith("📅 Je n'ai pas compris « 31/02 »")
