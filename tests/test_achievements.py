"""Succès (P18): the hourly check, its announcement, /succes and the badges shown."""

from datetime import UTC, datetime, timedelta

import pytest
from discord_fakes import FakeBot, FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

from extensions.achievements import (
    ANNOUNCED_AT_MOST,
    AchievementsExtension,
    announcement_lines,
    badges_text,
    seniors_of,
)
from extensions.profile import footprint_lines
from features.achievements import BY_KEY, active_badges
from features.profile import Footprint
from src.core import clock

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


class MemoryBadges:
    """What the extension asks of AchievementsRepository, in memory."""

    def __init__(self, qualifying=None):
        self.won: dict[tuple[str, str], datetime] = {}
        self.seeds: set[str] = set()
        self.qualifying = qualifying or {}

    async def seeded(self):
        return set(self.seeds)

    async def mark_seeded(self, badge, at):
        self.seeds.add(badge)

    async def award(self, badge, user_ids, at):
        new = [uid for uid in sorted(user_ids) if (uid, badge) not in self.won]
        for uid in new:
            self.won[uid, badge] = at
        return new

    async def badges_of(self, user_id):
        return {badge: at for (uid, badge), at in self.won.items() if uid == user_id}

    def __getattr__(self, name):
        if name.startswith("qualifying_"):
            key = name.removeprefix("qualifying_")

            async def who():
                return set(self.qualifying.get(key, ()))

            return who
        raise AttributeError(name)


@pytest.fixture
def world(store_config, monkeypatch):
    servers = {
        "1": {
            "moduleAchievements": {"enabled": True, "achievementsChannelId": "40"},
            "moduleStarboard": {"enabled": True},
        }
    }
    store_config({"servers": servers})
    guild = FakeGuild(1, "Les Copains")
    channel = guild.add_channel(FakeChannel(40, "succès"))
    camille = guild.add_member(FakeMember(10, "Camille"))
    camille.joined_at = NOW - timedelta(days=400)
    hugo = guild.add_member(FakeMember(11, "Hugo"))
    hugo.joined_at = NOW - timedelta(days=10)
    repo = MemoryBadges({"star": {"11"}})
    ext = bare_extension(AchievementsExtension, FakeBot(guild), repository=lambda gid: repo)
    monkeypatch.setattr(clock, "now", lambda: NOW)
    return ext, guild, channel, repo, servers


def test_only_the_badges_whose_module_is_on_and_left_on(world):
    *_, servers = world
    assert [b.key for b in active_badges("1")] == ["star", "seniority"]
    servers["1"]["moduleAchievements"]["badgeStar"] = False
    assert [b.key for b in active_badges("1")] == ["seniority"]
    servers["1"]["moduleAchievements"]["enabled"] = False
    assert active_badges("1") == []


def test_a_year_on_the_server_and_no_bot(world):
    _, guild, *_ = world
    assert seniors_of(guild, NOW) == {"10"}  # Hugo is new, Michel is a bot


async def test_the_first_run_is_silent_then_new_badges_are_announced(world):
    ext, guild, channel, repo, servers = world
    await AchievementsExtension.check_all.callback(ext)
    assert channel.sent == []  # Camille's year and Hugo's star were already there
    assert repo.won.keys() == {("10", "seniority"), ("11", "star")}

    repo.qualifying["star"] = {"10", "11"}
    await AchievementsExtension.check_all.callback(ext)
    (post,) = channel.sent
    assert post.embeds[0].description == "<@10> débloque ⭐ **Étoilé·e**"
    assert post.sent_with["allowed_mentions"] is not None  # named, not pinged

    await AchievementsExtension.check_all.callback(ext)
    assert len(channel.sent) == 1  # nothing new, nothing said


async def test_no_channel_no_announcement(world):
    ext, guild, channel, repo, servers = world
    servers["1"]["moduleAchievements"]["achievementsChannelId"] = ""
    repo.seeds = {"star", "seniority"}
    assert len(await ext.check_guild("1", servers["1"]["moduleAchievements"])) == 2
    assert channel.sent == []


def test_a_long_announcement_counts_the_rest():
    news = [(str(uid), BY_KEY["star"]) for uid in range(10, 10 + ANNOUNCED_AT_MOST + 3)]
    lines = announcement_lines(news)
    assert len(lines) == ANNOUNCED_AT_MOST + 1
    assert lines[-1] == "… et 3 autres."


async def test_succes_shows_won_and_left_to_win(world):
    ext, guild, channel, repo, _ = world
    repo.won["11", "star"] = NOW
    ctx = FakeCtx(guild.get_member(11), channel)
    await run(AchievementsExtension.succes, ext, ctx)
    (reply,) = ctx.replies
    embed = reply["embeds"][0]
    assert embed.description == "**1** badge sur 2."
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Débloqués"] == "⭐ Étoilé·e — le 22/11/2026"
    assert fields["À débloquer"] == "🎂 Vieille branche : un an sur le serveur"
    assert reply["ephemeral"] is True  # your own: for you


async def test_succes_of_someone_else_is_public_and_a_bot_has_none(world):
    ext, guild, channel, repo, _ = world
    ctx = FakeCtx(guild.get_member(11), channel)
    await run(AchievementsExtension.succes, ext, ctx, member=guild.get_member(10))
    assert ctx.replies[0]["ephemeral"] is False
    assert ctx.replies[0]["embeds"][0].title == "Succès de Camille"
    ctx = FakeCtx(guild.get_member(11), channel)
    await run(AchievementsExtension.succes, ext, ctx, member=guild.me)
    assert "bots" in ctx.last


async def test_succes_where_no_badge_is_on_says_who_can_change_it(world):
    ext, guild, channel, repo, servers = world
    servers["1"]["moduleAchievements"].update(badgeStar=False, badgeSeniority=False)
    ctx = FakeCtx(guild.get_member(11), channel)
    await run(AchievementsExtension.succes, ext, ctx)
    assert "dashboard" in ctx.last


def test_the_profile_lists_the_badges_won():
    lines = footprint_lines(Footprint(), None, None, [BY_KEY["star"], BY_KEY["dj"]])
    assert lines == ["🏆 ⭐ Étoilé·e · 🎵 DJ"]


def test_badges_text_dates_in_paris():
    done, todo = badges_text({"star": datetime(2026, 11, 21, 23, 30, tzinfo=UTC)}, [BY_KEY["star"]])
    assert done == ["⭐ Étoilé·e — le 22/11/2026"] and todo == []
