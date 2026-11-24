"""L'agenda (P19): its days, its sources, /agenda, the message kept up to date, the calendar."""

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from discord_fakes import (
    FakeBot,
    FakeChannel,
    FakeCtx,
    FakeGuild,
    FakeMember,
    FakeMessage,
    bare_extension,
    run,
)
from fastapi import HTTPException
from interactions import MessageFlags

import extensions.agenda as agenda_module
import src.discord_ext.agenda as gather_module
from extensions.agenda import AgendaExtension
from features.agenda import Entry, bounded_days, by_day, day_label, timed, window
from src.core import clock
from src.webui.routes.agenda import agenda_on_bot

# Sunday 22 November 2026, 18:00 in Paris.
NOW = clock.at_local(date(2026, 11, 22), 18)
TODAY = date(2026, 11, 22)


def test_days_are_named_like_people_say_them():
    assert day_label(TODAY, TODAY) == "Aujourd'hui"
    assert day_label(TODAY + timedelta(days=1), TODAY) == "Demain"
    assert day_label(date(2026, 12, 1), TODAY) == "Mardi 1er décembre"


def test_the_window_ends_with_its_last_day_and_days_are_bounded():
    start, end = window(NOW, 14)
    assert start == NOW and end == clock.at_local(date(2026, 12, 6))
    assert bounded_days("abc") == 14 and bounded_days(1) == 3 and bounded_days(90) == 31


def test_by_day_puts_whole_days_first_then_the_hours():
    entries = [
        timed("poll", "Fin du sondage « Pizza ? »", clock.at_local(TODAY, 21, 30)),
        Entry(TODAY, "birthday", "Anniversaire de Léa"),
        timed(
            "event",
            "Soirée jeux",
            clock.at_local(date(2026, 11, 24), 20),
            "https://discord.com/events/1/2",
        ),
    ]
    assert by_day(entries, TODAY) == [
        ("Aujourd'hui", ["🎂 Anniversaire de Léa", "📊 21:30 · Fin du sondage « Pizza ? »"]),
        ("Mardi 24 novembre", ["📅 20:00 · [Soirée jeux](https://discord.com/events/1/2)"]),
    ]


def test_a_crowded_day_says_what_it_left_out():
    entries = [Entry(TODAY, "birthday", f"Anniversaire de m{i:02}") for i in range(13)]
    ((_, lines),) = by_day(entries, TODAY)
    assert len(lines) == 11 and lines[-1] == "… et 3 de plus"


class MemoryAgenda:
    def __init__(self, guild_id):
        pass

    async def birth_dates(self):
        return [(10, date(1990, 11, 23)), (11, date(2001, 3, 1)), (77, date(1999, 11, 24))]

    async def giveaways_ending(self, start, end):
        return [
            {
                "prize": "Un jeu",
                "ends_at": NOW + timedelta(days=2),
                "channel_id": "40",
                "message_id": "5",
            }
        ]

    async def polls_closing(self, start, end):
        return [
            {
                "question": "Pizza ?",
                "closes_at": NOW + timedelta(hours=3),
                "channel_id": "40",
                "message_id": "6",
            }
        ]

    async def santa_exchange(self):
        return date(2026, 12, 19)  # past the 14 days


@pytest.fixture
def world(store_config, monkeypatch):
    servers = {
        "1": {
            "moduleAgenda": {"enabled": True, "agendaChannelId": "40", "agendaDays": 14},
            "moduleBirthday": {"enabled": True},
            "moduleGiveaway": {"enabled": True},
            "moduleUtils": {"enabled": True},
            "moduleSecretSanta": {"enabled": True},
        }
    }
    store_config({"servers": servers})
    guild = FakeGuild(1, "Les Copains")
    channel = guild.add_channel(FakeChannel(40, "agenda"))
    guild.add_member(FakeMember(10, "Léa"))
    guild.add_member(FakeMember(11, "Hugo"))

    async def events():
        return [
            SimpleNamespace(
                id=3,
                name="Soirée jeux",
                status=1,
                start_time=clock.at_local(date(2026, 11, 24), 20),
            ),
            SimpleNamespace(id=4, name="Finie", status=3, start_time=NOW - timedelta(hours=1)),
            SimpleNamespace(id=5, name="En cours", status=2, start_time=NOW - timedelta(hours=1)),
            SimpleNamespace(id=6, name="Trop loin", status=1, start_time=NOW + timedelta(days=40)),
        ]

    guild.list_scheduled_events = events
    monkeypatch.setattr(gather_module, "AgendaRepository", MemoryAgenda)
    monkeypatch.setattr(clock, "now", lambda: NOW)
    return SimpleNamespace(guild=guild, channel=channel, bot=FakeBot(guild), servers=servers)


async def test_the_agenda_gathers_every_source_on(world):
    agenda = await gather_module.gather_agenda(world.bot, "1", NOW, 14)
    titles = sorted(e.title for e in agenda.entries)
    assert titles == [
        "Anniversaire de Léa",  # Hugo's is in March, 77 left the server
        "En cours",
        "Fin du giveaway « Un jeu »",
        "Fin du sondage « Pizza ? »",
        "Soirée jeux",
    ]
    ongoing = next(e for e in agenda.entries if e.title == "En cours")
    assert ongoing.at == NOW and ongoing.url == "https://discord.com/events/1/5"
    assert agenda.unavailable == []


async def test_a_module_off_is_not_read_and_a_failing_source_is_named(world, monkeypatch):
    world.servers["1"]["moduleGiveaway"]["enabled"] = False

    async def broken():
        raise RuntimeError("Discord ne répond pas")

    world.guild.list_scheduled_events = broken
    agenda = await gather_module.gather_agenda(world.bot, "1", NOW, 14)
    assert not any(e.kind in ("giveaway", "event") for e in agenda.entries)
    assert agenda.unavailable == ["event"]


async def test_agenda_command_answers_the_member_alone(world):
    ext = bare_extension(AgendaExtension, world.bot)
    ctx = FakeCtx(world.guild.get_member(10), world.channel, bot=world.bot)
    await run(AgendaExtension.agenda, ext, ctx)
    (reply,) = ctx.replies
    assert reply["ephemeral"] is True
    embed = reply["embeds"][0]
    assert embed.title == "🗓️ Les 14 prochains jours"
    assert [f.name for f in embed.fields] == ["Aujourd'hui", "Demain", "Mardi 24 novembre"]


async def test_the_message_is_kept_up_to_date_and_pinned(world, monkeypatch):
    message = FakeMessage(content="🗓️ L'agenda arrive…")
    asked = {}

    async def fake_fetch(bot, **kwargs):
        asked.update(kwargs)
        return message

    monkeypatch.setattr(agenda_module, "fetch_or_create_persistent_message", fake_fetch)
    ext = bare_extension(AgendaExtension, world.bot)
    await AgendaExtension.refresh_all.callback(ext)
    assert asked["slot"] == "agenda" and asked["channel_id"] == "40" and asked["pin"] is True
    assert asked["v2"] is True
    assert message.flags & MessageFlags.IS_COMPONENTS_V2
    (container,) = message.components
    texts = [c.content for c in container.components if hasattr(c, "content")]
    assert texts[0] == "-# 🗓️ Agenda\n## 🗓️ Les 14 prochains jours"
    assert texts[1].startswith(
        "**Aujourd'hui**\n📅 18:00 · [En cours](https://discord.com/events/1/5)"
    )
    assert texts[-1] == "-# Michel · Agenda · mis à jour toutes les 30 minutes"
    settings = world.servers["1"]["moduleAgenda"]
    assert await ext.refresh("1", settings) is False  # nothing changed, no edit


async def test_no_channel_no_message(world, monkeypatch):
    world.servers["1"]["moduleAgenda"]["agendaChannelId"] = ""

    async def fail(*args, **kwargs):
        raise AssertionError("no message without a channel")

    monkeypatch.setattr(agenda_module, "fetch_or_create_persistent_message", fail)
    await AgendaExtension.refresh_all.callback(bare_extension(AgendaExtension, world.bot))


async def test_the_calendar_route_gives_days_and_names_never_ids(world):
    out = await agenda_on_bot(world.bot, "1")
    assert out["days"] == 14 and out["today"] == "2026-11-22"
    first = out["entries"][0]
    assert first == {
        "day": "2026-11-22",
        "dayLabel": "Aujourd'hui",
        "kind": "event",
        "icon": "📅",
        "title": "En cours",
        "time": "18:00",
        "url": "https://discord.com/events/1/5",
    }
    assert "10" not in str([e["title"] for e in out["entries"]])


async def test_the_calendar_route_says_when_the_agenda_is_off(world):
    world.servers["1"]["moduleAgenda"]["enabled"] = False
    with pytest.raises(HTTPException) as caught:
        await agenda_on_bot(world.bot, "1")
    assert caught.value.status_code == 404
