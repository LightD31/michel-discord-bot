"""The badges' store and who qualifies, against a real MongoDB (P18)."""

from datetime import UTC, datetime, timedelta

import pytest

from features.achievements import BY_KEY, AchievementsRepository, check_badges
from features.achievements.badges import DJ_TRACKS, FEUR_COUNT, VOICE_TICKS

pytestmark = pytest.mark.mongo

ME = "111111111111111111"
OTHER = "222222222222222222"
NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


async def test_a_badge_is_awarded_once_and_keeps_its_day(mongo):
    repo = AchievementsRepository(mongo.guild_id)
    assert await repo.award("star", [ME, OTHER], NOW) == [ME, OTHER]
    later = NOW + timedelta(days=3)
    assert await repo.award("star", [ME], later) == []  # already theirs
    assert await repo.award("star", [ME, "0123", "abc", ""], later) == []  # nobody else
    assert await repo.badges_of(ME) == {"star": NOW}
    assert await repo.badges_of(OTHER) == {"star": NOW}
    await repo.award("dj", [OTHER], NOW)
    assert await repo.holders() == {"star": 2, "dj": 1}


async def test_badges_of_many_answers_for_each_member(mongo):
    repo = AchievementsRepository(mongo.guild_id)
    await repo.award("star", [ME], NOW)
    await repo.award("dj", [ME], NOW)
    found = await repo.badges_of_many([ME, OTHER])
    assert sorted(found[ME]) == ["dj", "star"]
    assert found[OTHER] == []


async def test_who_qualifies_reads_what_the_modules_keep(mongo):
    g = mongo.guild_collection
    await g("starboard").insert_many([{"author_id": ME}, {"author_id": "0"}])
    await g("xp_events").insert_many(
        [{"user_id": ME, "source": "voice"} for _ in range(VOICE_TICKS)]
        + [{"user_id": OTHER, "source": "voice"} for _ in range(VOICE_TICKS - 1)]
        + [{"user_id": OTHER} for _ in range(VOICE_TICKS)]  # messages don't count
    )
    await g("playlistItemsFull").insert_many(
        [{"added_by": OTHER} for _ in range(DJ_TRACKS)]
        + [{"added_by": ME} for _ in range(DJ_TRACKS - 1)]
    )
    await g("suggestions").insert_many(
        [
            {"author_id": ME, "status": "approved"},
            {"author_id": OTHER, "status": "implemented"},
        ]
    )
    await g("feur_stats").insert_many(
        [
            {"_id": f"user_{ME}", "total": FEUR_COUNT},
            {"_id": f"user_{OTHER}", "total": FEUR_COUNT - 1},
            {"_id": "guild_total", "total": 10_000},
        ]
    )
    repo = AchievementsRepository(mongo.guild_id)
    assert await repo.qualifying_star() == {ME}
    assert await repo.qualifying_voice() == {ME}
    assert await repo.qualifying_dj() == {OTHER}
    assert await repo.qualifying_ideas() == {OTHER}
    assert await repo.qualifying_feur() == {ME}


async def test_the_first_check_of_a_badge_is_silent(mongo):
    g = mongo.guild_collection
    await g("starboard").insert_one({"author_id": ME})
    repo = AchievementsRepository(mongo.guild_id)
    badges = [BY_KEY["star"], BY_KEY["seniority"]]

    assert await check_badges(repo, badges, {ME}, NOW) == []  # already there: no fanfare
    assert await repo.seeded() == {"star", "seniority"}

    await g("starboard").insert_one({"author_id": OTHER})
    news = await check_badges(repo, badges, {ME, OTHER}, NOW + timedelta(hours=1))
    assert sorted((uid, badge.key) for uid, badge in news) == [
        (OTHER, "seniority"),
        (OTHER, "star"),
    ]
    # Nothing new: nothing to say.
    assert await check_badges(repo, badges, {ME, OTHER}, NOW + timedelta(hours=2)) == []


async def test_a_badge_switched_on_later_is_silent_too(mongo):
    g = mongo.guild_collection
    await g("feur_stats").insert_one({"_id": f"user_{ME}", "total": FEUR_COUNT})
    repo = AchievementsRepository(mongo.guild_id)
    await check_badges(repo, [BY_KEY["star"]], set(), NOW)
    assert await check_badges(repo, [BY_KEY["star"], BY_KEY["feur"]], set(), NOW) == []
    assert await repo.badges_of(ME) == {"feur": NOW}
