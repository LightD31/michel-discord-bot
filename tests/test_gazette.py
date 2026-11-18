"""La Gazette (A1): its week, its sections, and the weekly post made once."""

from datetime import date, timedelta

import pytest
from discord_fakes import FakeBot, FakeChannel, FakeGuild, bare_extension, discord_error
from interactions.client.errors import Forbidden

import extensions.gazette as gazette_module
from extensions.gazette import GazetteExtension
from features.gazette import Edition, is_due, render, upcoming_birthdays, week_key
from features.gazette.edition import week_label
from src.core import clock

SUNDAY_7PM = clock.at_local(date(2026, 11, 22), 19)


def test_the_week_and_when_it_is_due():
    assert week_key(SUNDAY_7PM) == "2026-W47"
    assert is_due(SUNDAY_7PM, 7, 18) and not is_due(SUNDAY_7PM, 7, 20)
    assert not is_due(SUNDAY_7PM, 1, 0)  # Monday's paper
    assert week_label(SUNDAY_7PM - timedelta(days=7), SUNDAY_7PM) == "du 15 au 22 novembre"
    assert week_label(clock.at_local(date(2026, 11, 28)), clock.at_local(date(2026, 12, 3))) == (
        "du 28 novembre au 2 décembre"
    )


def test_birthdays_to_come_never_say_the_year():
    days = [
        ("Léa", date(1990, 11, 24)),
        ("Hugo", date(2001, 11, 19)),  # passed this year: next year, out of the week
        ("Zoé", date(2000, 2, 29)),
        ("Tom", date(1985, 1, 2)),
    ]
    assert upcoming_birthdays(days, date(2026, 11, 22)) == [("Léa", date(2026, 11, 24))]
    assert upcoming_birthdays(days, date(2027, 2, 25)) == [("Zoé", date(2027, 2, 28))]
    assert upcoming_birthdays(days, date(2026, 12, 30)) == [("Tom", date(2027, 1, 2))]


def test_a_full_week_and_a_quiet_one():
    start = SUNDAY_7PM - timedelta(days=7)
    quiet = render(Edition("Les Copains", start, SUNDAY_7PM))
    assert quiet[0] == "📰 La Gazette de Les Copains"
    assert quiet[1].endswith("Une semaine calme : rien à raconter cette fois-ci.")
    assert quiet[2] == []
    full = Edition(
        "Les Copains",
        start,
        SUNDAY_7PM,
        top_members=[("Camille", 1200), ("Hugo", 300), ("Léa", 20), ("Tom", 5)],
        star=("Hugo", 7, "https://discord.com/channels/1/2/3"),
        birthdays=[("Léa", date(2026, 11, 24))],
        newcomers=[f"n{i}" for i in range(12)],
        tracks_added=3,
        top_dj=("Camille", 2),
        suggestions=[(12, "implemented", "Un salon musique")],
        giveaways=[("Un jeu", ["Tom"]), ("Un autre", [])],
    )
    fields = dict(render(full)[2])
    assert fields["🔥 Les plus actifs"].splitlines()[0] == "🥇 Camille — 1 200 XP"
    assert fields["🔥 Les plus actifs"].splitlines()[3] == "4. Tom — 5 XP"
    assert fields["⭐ Le message de la semaine"] == (
        "[Celui de Hugo](https://discord.com/channels/1/2/3), 7 ⭐"
    )
    assert fields["👋 Bienvenue"].endswith("n9 et 2 autres")
    assert fields["🎂 Anniversaires à venir"] == "Léa — mardi 24 novembre"
    assert fields["🎵 La playlist"] == "3 titres ajoutés, dont 2 par Camille."
    assert fields["💡 Suggestions tranchées"] == "#12 🚀 mise en place : Un salon musique"
    assert fields["🎁 Giveaways"] == "Un jeu → Tom\nUn autre → personne"


class MemoryEditions:
    def __init__(self):
        self.claimed = {}

    async def claim_edition(self, key, now):
        if key in self.claimed:
            return False
        self.claimed[key] = {}
        return True

    async def release_edition(self, key):
        if "message_id" not in self.claimed.get(key, {}):
            self.claimed.pop(key, None)

    async def set_edition_message(self, key, channel_id, message_id):
        self.claimed[key] = {"channel_id": channel_id, "message_id": message_id}


@pytest.fixture
def world(store_config, monkeypatch):
    settings = {"enabled": True, "gazetteChannelId": "40", "gazetteWeekday": 7, "gazetteHour": 18}
    store_config({"servers": {"1": {"moduleGazette": settings}}})
    guild = FakeGuild(1, "Les Copains")
    channel = guild.add_channel(FakeChannel(40, "gazette"))
    editions = MemoryEditions()
    ext = bare_extension(GazetteExtension, FakeBot(guild), repository=lambda gid: editions)

    async def gather(bot, guild_id, now):
        return Edition("Les Copains", now - timedelta(days=7), now, top_members=[("Camille", 9)])

    monkeypatch.setattr(gazette_module, "gather_edition", gather)
    return ext, channel, editions, settings


async def test_the_weekly_task_posts_once_on_its_day(world, monkeypatch):
    ext, channel, editions, _ = world
    monkeypatch.setattr(clock, "now", lambda: SUNDAY_7PM - timedelta(hours=2))
    await GazetteExtension.publish_due.callback(ext)
    assert channel.sent == []  # 17:00: not yet
    monkeypatch.setattr(clock, "now", lambda: SUNDAY_7PM)
    await GazetteExtension.publish_due.callback(ext)
    await GazetteExtension.publish_due.callback(ext)  # 15 minutes later: already out
    (post,) = channel.sent
    assert post.embeds[0].title == "📰 La Gazette de Les Copains"
    assert post.sent_with["allowed_mentions"] is not None
    assert editions.claimed["2026-W47"]["message_id"] == str(post.id)


async def test_a_refused_post_gives_the_week_back(world):
    ext, channel, editions, settings = world

    async def refuse(*_, **__):
        raise discord_error()

    channel.send = refuse
    with pytest.raises(Forbidden):
        await ext.publish_week("1", settings, SUNDAY_7PM)
    assert editions.claimed == {}  # the next run tries again


async def test_a_missing_channel_gives_the_week_back(world):
    ext, channel, editions, settings = world
    assert not await ext.publish_week("1", {**settings, "gazetteChannelId": "999"}, SUNDAY_7PM)
    assert editions.claimed == {}
