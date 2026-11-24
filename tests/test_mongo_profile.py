"""/profil's counts against a real MongoDB: each module's own collection, by member."""

from datetime import UTC, datetime

import pytest

from features.profile import ProfileRepository
from features.profile.repository import SECTIONS

pytestmark = pytest.mark.mongo

ME = "111111111111111111"
OTHER = "222222222222222222"


async def test_the_footprint_counts_the_member_and_nobody_else(mongo):
    g = mongo.guild_collection
    await g("starboard").insert_many(
        [{"author_id": ME, "count": 5}, {"author_id": ME, "count": 3}, {"author_id": OTHER}]
    )
    await g("playlistItemsFull").insert_many([{"added_by": ME}, {"added_by": OTHER}])
    await g("suggestions").insert_many(
        [
            {"author_id": ME, "status": "implemented"},
            {"author_id": ME, "status": "approved"},
            {"author_id": ME, "status": "denied"},
        ]
    )
    await g("giveaways").insert_one({"winners": [OTHER, ME]})
    await g("birthday").insert_one(
        {"user": int(ME), "date": datetime(1990, 3, 12, tzinfo=UTC), "hideyear": True}
    )
    found = await ProfileRepository(mongo.guild_id).footprint(ME, SECTIONS)
    assert (found.stars, found.stars_received, found.tracks) == (2, 8, 1)
    assert (found.suggestions_done, found.giveaways_won) == (2, 1)
    assert found.birthday.month == 3 and found.birthday_hides_year

    nothing = await ProfileRepository(mongo.guild_id).footprint(ME, set())
    assert nothing.stars is None and nothing.birthday is None
