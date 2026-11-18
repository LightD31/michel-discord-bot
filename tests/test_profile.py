"""/profil and "📊 Profil": a member's card across modules (A2)."""

from datetime import UTC, datetime

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

import extensions.profile as profile_module
from extensions.profile import ProfileExtension, footprint_lines
from features.profile import Footprint


def test_each_line_shows_only_when_there_is_something():
    assert footprint_lines(Footprint(), None, None) == []
    found = Footprint(
        stars=2,
        stars_received=17,
        tracks=1,
        suggestions_done=3,
        giveaways_won=0,
        birthday=datetime(1990, 3, 1, tzinfo=UTC),
    )
    assert footprint_lines(found, "novembre 2026", 1234) == [
        "🏁 Saison novembre 2026 : **1 234 XP**",
        "⭐ 2 messages au starboard (17 ⭐)",
        "🎵 1 titre ajouté",
        "💡 3 suggestions retenues",
        "🎂 Anniversaire le 1er mars",  # never the year
    ]


class MemoryXp:
    def __init__(self, stats):
        self.stats = stats

    async def get_user(self, uid):
        return self.stats.get(uid)

    async def get_user_rank(self, uid):
        return 2

    async def season_xp(self, uid, start, end):
        return 80


class MemoryProfile:
    def __init__(self):
        self.asked = None

    async def footprint(self, uid, sections):
        self.asked = set(sections)
        return Footprint(stars=1 if "stars" in sections else None, stars_received=4)


@pytest.fixture
def world(store_config, monkeypatch):
    store_config(
        {
            "servers": {
                "1": {
                    "moduleXp": {"enabled": True, "seasonsEnabled": True},
                    "moduleStarboard": {"enabled": True, "starboardChannelId": "5"},
                    "moduleSpotify": {"enabled": False},
                }
            }
        }
    )
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    member = guild.add_member(FakeMember(7, "camille"))
    profile = MemoryProfile()
    xp = MemoryXp({"7": {"xp": 500, "msg": 40}})
    ctx = FakeCtx(member, channel)
    ext = bare_extension(
        ProfileExtension, ctx.bot, xp_repo=lambda gid: xp, profile_repo=lambda gid: profile
    )
    drawn = []

    def fake_card(**kwargs):
        drawn.append(kwargs)
        import io

        return io.BytesIO(b"png")

    monkeypatch.setattr(profile_module, "render_rank_card", fake_card)
    return ext, ctx, member, profile, xp, drawn


async def test_the_card_reads_only_the_modules_that_are_on(world):
    ext, ctx, member, profile, xp, drawn = world
    await run(ProfileExtension.profil, ext, ctx)
    assert profile.asked == {"stars"}  # Spotify is off here, the others unset
    reply = ctx.replies[-1]
    (embed,) = reply["embeds"]
    assert embed.title == "Profil de camille"
    assert "🏁 Saison" in embed.description and "80 XP" in embed.description
    assert "⭐ 1 message au starboard (4 ⭐)" in embed.description
    assert drawn[0]["level"] >= 1 and drawn[0]["rank"] == 2
    assert not reply["ephemeral"]


async def test_the_right_click_card_is_private_and_bots_have_none(world):
    ext, ctx, member, *_ = world
    ctx.target = member
    await run(ProfileExtension.profile_menu, ext, ctx)
    assert ctx.replies[-1]["ephemeral"]
    ctx.target = FakeMember(99, "Michel", bot=True)
    await run(ProfileExtension.profile_menu, ext, ctx)
    assert ctx.last.startswith("🤖 Les bots n'ont pas de profil")


async def test_a_member_without_xp_still_has_a_card(world):
    ext, ctx, member, profile, xp, drawn = world
    xp.stats = {}
    await run(ProfileExtension.profil, ext, ctx)
    (embed,) = ctx.replies[-1]["embeds"]
    assert embed.description.startswith("Pas encore d'XP") and drawn == []
