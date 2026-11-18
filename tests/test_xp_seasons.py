"""XP seasons (A4): bounds on the Paris clock, the podium and the champion role."""

from datetime import date, timedelta

import pytest
from discord_fakes import FakeChannel, FakeGuild, FakeMember, FakeRole, bare_extension
from interactions import Permissions

from extensions.xp import XpExtension
from extensions.xp.seasons import podium_embed
from features.xp.seasons import clamp_months, podium_lines, previous_season, season_of, thousands
from src.core import clock


def at(y, m, d, h=12):
    return clock.at_local(date(y, m, d), h)


@pytest.mark.parametrize(
    ("months", "moment", "key", "label", "start", "end"),
    [
        (1, at(2026, 11, 18), "2026-11", "novembre 2026", (2026, 11, 1), (2026, 12, 1)),
        (1, at(2026, 12, 31, 23), "2026-12", "décembre 2026", (2026, 12, 1), (2027, 1, 1)),
        (3, at(2026, 11, 18), "2026-Q4", "T4 2026", (2026, 10, 1), (2027, 1, 1)),
        (12, at(2026, 3, 1), "2026-01+12", "l'année 2026", (2026, 1, 1), (2027, 1, 1)),
        (5, at(2026, 11, 18), "2026-11+5", "novembre – décembre 2026", (2026, 11, 1), (2027, 1, 1)),
        (2, at(2026, 4, 2), "2026-03+2", "mars – avril 2026", (2026, 3, 1), (2026, 5, 1)),
    ],
)
def test_seasons_follow_the_calendar(months, moment, key, label, start, end):
    season = season_of(moment, months)
    assert (season.key, season.label) == (key, label)
    assert season.start == clock.at_local(date(*start))
    assert season.end == clock.at_local(date(*end))


def test_a_season_starts_at_midnight_in_paris_not_utc():
    # 1 November 00:30 in Paris is still 31 October in UTC.
    assert season_of(clock.at_local(date(2026, 11, 1), 0, 30), 1).key == "2026-11"


def test_the_previous_season_crosses_the_year():
    assert previous_season(at(2027, 1, 1, 0), 1).key == "2026-12"
    assert previous_season(at(2026, 11, 18), 3).key == "2026-Q3"


def test_the_length_is_clamped():
    assert [clamp_months(v) for v in (0, 1, 7, 13, "x", None)] == [1, 1, 7, 12, 1, 1]


def test_the_podium_reads_well():
    lines = podium_lines([("1", 12345), ("2", 50), ("3", 7), ("4", 1)], {"1": "Camille"})
    assert lines == ["🥇 Camille — 12 345 XP", "🥈 <@2> — 50 XP", "🥉 <@3> — 7 XP"]
    assert thousands(999) == "999"
    season = season_of(at(2026, 11, 18), 1)
    embed = podium_embed(season, [(str(i), 100 - i) for i in range(1, 7)], {})
    assert embed.title == "🏆 Fin de la saison : novembre 2026"
    assert embed.fields[0].value.splitlines()[0] == "4. <@4>"


class MemoryXp:
    def __init__(self, ranking):
        self.ranking = ranking
        self.seasons = {}
        self.asked = []

    async def get_season(self, key):
        return self.seasons.get(key)

    async def season_ranking(self, start, end, limit=None):
        self.asked.append((start, end))
        return self.ranking[:limit]

    async def record_season(self, key, label, top, now):
        if key in self.seasons:
            return False
        self.seasons[key] = {"label": label, "top": top}
        return True


@pytest.fixture
def world(store_config):
    guild = FakeGuild(1)
    board = guild.add_channel(FakeChannel(30, "classement"))
    champion = guild.add_role(FakeRole(500, name="Champion", position=5))
    old = guild.add_member(FakeMember(8, "ancien", roles=[champion]))
    winner = guild.add_member(FakeMember(7, "camille"))
    winner.display_name = "Camille"
    settings = {
        "enabled": True,
        "seasonsEnabled": True,
        "seasonMonths": 1,
        "xpChannelId": "30",
        "seasonChampionRoleId": "500",
    }
    store_config({"servers": {"1": {"moduleXp": settings}}})
    repo = MemoryXp([("7", 900), ("8", 400), ("9", 10)])
    from discord_fakes import FakeBot

    ext = bare_extension(XpExtension, FakeBot(guild), _repo=lambda gid: repo)
    return ext, repo, board, guild, settings, old, winner, champion


async def test_a_season_closes_once_with_its_podium_and_champion(world, monkeypatch):
    import extensions.xp.seasons as seasons_module

    monkeypatch.setattr(
        seasons_module, "grantable_role", lambda guild, rid: (guild.get_role(rid), None)
    )
    ext, repo, board, guild, settings, old, winner, champion = world
    now = at(2026, 12, 1, 0)
    assert await ext.close_season("1", settings, now)
    assert repo.seasons["2026-11"]["top"][0] == ("7", 900)
    (post,) = board.sent
    assert post.embeds[0].description.startswith("🥇 Camille — 900 XP")
    assert post.sent_with["allowed_mentions"] is not None
    assert winner.has_role(champion) and not old.has_role(champion)
    # Again (the task ran twice, or the bot restarted): nothing more.
    assert not await ext.close_season("1", settings, now + timedelta(hours=1))
    assert len(board.sent) == 1


async def test_a_season_long_gone_is_not_announced(world):
    ext, repo, board, *_ = world
    settings = world[4]
    assert not await ext.close_season("1", settings, at(2026, 12, 20))
    assert repo.seasons == {} and board.sent == []


async def test_a_refused_champion_role_is_left_alone(world):
    ext, repo, board, guild, settings, old, winner, champion = world
    # The bot sits below the role (position 5 > the fake bot's 50? no: make it higher).
    champion.position = 90
    champion.permissions = Permissions.ADMINISTRATOR
    await ext.close_season("1", settings, at(2026, 12, 1, 0))
    assert not winner.has_role(champion) and old.has_role(champion)
    assert len(board.sent) == 1  # the podium is posted anyway


async def test_the_season_leaderboard_shows_this_seasons_xp(world):
    from features.xp import TTLCache

    ext, repo, board, guild, settings, old, winner, champion = world
    ext._user_cache = TTLCache(ttl=60)
    season = season_of(clock.now(), 1)
    (page,) = await ext._build_season_embeds(guild, season)
    assert page.title.startswith(f"Saison {season.label}")
    assert [f.value for f in page.fields] == ["`900 XP cette saison`", "`400 XP cette saison`"]
    assert repo.asked[-1] == (season.start, season.end)
    repo.ranking = []
    (empty,) = await ext._build_season_embeds(guild, season)
    assert empty.description.startswith("Personne n'a encore gagné d'XP")


def test_the_leaderboard_builders_are_the_real_ones():
    # A stub on CommandsMixin (earlier in the MRO) would shadow them and return None.
    # By name: another test may have reloaded the package (new class objects).
    for name in ("_build_leaderboard_embeds", "_build_season_embeds"):
        assert getattr(XpExtension, name).__qualname__ == f"LeaderboardMixin.{name}"
