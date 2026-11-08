"""The stored member list userinfo keeps, against a real MongoDB (see tests/conftest.py)."""

import pytest

from features.userinfo.repository import UserInfoRepository

pytestmark = pytest.mark.mongo


async def test_every_stored_member_is_listed_with_its_names(mongo):
    repo = UserInfoRepository(mongo.guild_id)
    await repo.bulk_upsert(
        [
            {"id": "1", "username": "alice", "display_name": "Élodie"},
            {"id": "2", "username": "bob_42", "display_name": "Bob"},
        ]
    )
    await repo.upsert("2", "bob_42", "Bobby")
    await repo.delete("1")
    await mongo.guild_collection("users").insert_one({"_id": "3"})

    rows = sorted(await repo.all_members(), key=lambda r: r["id"])

    assert rows == [
        {"id": "2", "username": "bob_42", "display_name": "Bobby"},
        {"id": "3", "username": "", "display_name": ""},
    ]
