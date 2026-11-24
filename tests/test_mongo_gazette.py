"""La Gazette against a real MongoDB: one week of each module, members named."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from discord_fakes import FakeBot, FakeGuild, FakeMember

from features.feur import FeurRepository
from features.gazette import GazetteRepository
from features.xp import XpRepository
from src.core import clock
from src.discord_ext.gazette import gather_edition

pytestmark = pytest.mark.mongo

NOW = clock.at_local(date(2026, 11, 22), 19)
IN_WEEK = NOW - timedelta(days=2)
BEFORE = NOW - timedelta(days=8)


async def test_an_edition_is_claimed_once_and_given_back_before_its_post(mongo):
    repo = GazetteRepository(mongo.guild_id)
    claims = await asyncio.gather(*(repo.claim_edition("2026-W47", NOW) for _ in range(5)))
    assert claims.count(True) == 1
    await repo.release_edition("2026-W47")
    assert await repo.claim_edition("2026-W47", NOW)
    await repo.set_edition_message("2026-W47", "40", "900")
    await repo.release_edition("2026-W47")  # posted: kept
    assert not await repo.claim_edition("2026-W47", NOW)
    assert (await repo.last_edition())["message_id"] == "900"


async def test_the_week_of_every_module_on(mongo, store_config):
    gid = mongo.guild_id
    on = {"enabled": True}
    store_config(
        {
            "servers": {
                gid: {
                    name: dict(on)
                    for name in (
                        "moduleXp",
                        "moduleStarboard",
                        "moduleBirthday",
                        "moduleSpotify",
                        "moduleSuggestions",
                        "moduleGiveaway",
                        "moduleFeur",
                    )
                }
            }
        }
    )
    guild = FakeGuild(int(gid), "Les Copains")
    camille = guild.add_member(FakeMember(7, "camille"))
    hugo = guild.add_member(FakeMember(8, "hugo"))
    camille.joined_at = BEFORE
    hugo.joined_at = IN_WEEK
    g = mongo.guild_collection
    xp = XpRepository(gid)
    await xp.log_event("7", 50, 0, IN_WEEK)
    await xp.log_event("8", 80, 0, IN_WEEK)
    await xp.log_event("8", 999, 0, BEFORE)
    await xp.log_event("5", 999, 0, IN_WEEK)  # left the server: not in the paper
    await g("starboard").insert_many(
        [
            {"author_id": "7", "count": 4, "created_at": IN_WEEK, "channel_id": "10",
             "original_message_id": "500"},
            {"author_id": "8", "count": 9, "created_at": BEFORE, "channel_id": "10",
             "original_message_id": "501"},
        ]
    )  # fmt: skip
    await g("birthday").insert_one({"user": 8, "date": datetime(1999, 11, 24, tzinfo=UTC)})
    await g("playlistItemsFull").insert_many(
        [
            {"added_by": "7", "added_at": IN_WEEK.isoformat()},
            {"added_by": "7", "added_at": IN_WEEK},
            {"added_by": "8", "added_at": BEFORE.isoformat()},
        ]
    )
    await g("suggestions").insert_one(
        {"id": 3, "status": "approved", "text": "Un salon", "decided_at": IN_WEEK}
    )
    await g("giveaways").insert_many(
        [
            {"prize": "Un jeu", "winners": ["8"], "ends_at": IN_WEEK, "drawn": True},
            {"prize": "Annulé", "winners": [], "ends_at": IN_WEEK, "drawn": True,
             "cancelled": True},
        ]
    )  # fmt: skip

    feur = FeurRepository(gid)
    await feur.ensure_indexes()
    for who, when in (("5", IN_WEEK), ("5", IN_WEEK), ("7", IN_WEEK), ("8", BEFORE), ("8", BEFORE)):
        await feur.record_event(who, "feur", when)  # 5 left the server: 7 is the one named

    edition = await gather_edition(FakeBot(guild), gid, NOW)
    assert edition.feur == ("camille", 1)
    assert edition.top_members == [("hugo", 80), ("camille", 50)]
    assert edition.star == ("camille", 4, f"https://discord.com/channels/{gid}/10/500")
    assert edition.birthdays == [("hugo", date(2026, 11, 24))]
    assert (edition.tracks_added, edition.top_dj) == (2, ("camille", 2))
    assert edition.suggestions == [(3, "approved", "Un salon")]
    assert edition.giveaways == [("Un jeu", ["hugo"])]
    assert edition.newcomers == ["hugo"]


async def test_a_module_switched_off_says_nothing(mongo, store_config):
    gid = mongo.guild_id
    store_config({"servers": {gid: {"moduleXp": {"enabled": False}}}})
    await XpRepository(gid).log_event("7", 50, 0, IN_WEEK)
    guild = FakeGuild(int(gid))
    guild.add_member(FakeMember(7, "camille"))
    edition = await gather_edition(FakeBot(guild), gid, NOW)
    assert edition.top_members == []


async def test_feur_events_count_by_week_and_expire(mongo):
    repo = FeurRepository(mongo.guild_id)
    await repo.ensure_indexes()
    await repo.record_event("7", "feur", IN_WEEK)
    await repo.record_event("7", "pour_feur", IN_WEEK)
    await repo.record_event("8", "feur", BEFORE)
    assert await repo.counts_between(NOW - timedelta(days=7), NOW) == {"7": 2}
    assert (await repo.get_user_stats("7")).total == 2
    indexes = await mongo.guild_collection("feur_events").index_information()
    assert indexes["at_ttl"]["expireAfterSeconds"] == 8 * 24 * 3600
