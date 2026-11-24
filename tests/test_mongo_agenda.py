"""What the agenda reads, against a real MongoDB (P19)."""

from datetime import UTC, datetime, timedelta

import pytest

from features.agenda import AgendaRepository

pytestmark = pytest.mark.mongo

NOW = datetime(2026, 11, 22, 17, tzinfo=UTC)
END = NOW + timedelta(days=14)


async def test_open_giveaways_and_polls_in_the_window_only(mongo):
    g = mongo.guild_collection
    base = {"channel_id": "40", "message_id": "5"}
    await g("giveaways").insert_many(
        [
            {
                **base,
                "prize": "dans la fenêtre",
                "ends_at": NOW + timedelta(days=1),
                "drawn": False,
                "cancelled": False,
            },
            {
                **base,
                "prize": "tiré",
                "ends_at": NOW + timedelta(days=1),
                "drawn": True,
                "cancelled": False,
            },
            {
                **base,
                "prize": "annulé",
                "ends_at": NOW + timedelta(days=1),
                "drawn": False,
                "cancelled": True,
            },
            {
                **base,
                "prize": "trop loin",
                "ends_at": END + timedelta(days=1),
                "drawn": False,
                "cancelled": False,
            },
        ]
    )
    await g("polls").insert_many(
        [
            {**base, "question": "ouvert", "closes_at": NOW + timedelta(hours=2), "closed": False},
            {**base, "question": "fermé", "closes_at": NOW + timedelta(hours=2), "closed": True},
            {**base, "question": "sans fin", "closes_at": None, "closed": False},
        ]
    )
    repo = AgendaRepository(mongo.guild_id)
    assert [d["prize"] for d in await repo.giveaways_ending(NOW, END)] == ["dans la fenêtre"]
    assert [d["question"] for d in await repo.polls_closing(NOW, END)] == ["ouvert"]


async def test_birth_dates_and_no_santa(mongo):
    await mongo.guild_collection("birthday").insert_one(
        {"user": 10, "date": datetime(1990, 11, 23, tzinfo=UTC)}
    )
    repo = AgendaRepository(mongo.guild_id)
    assert [uid for uid, _ in await repo.birth_dates()] == [10]
    assert await repo.santa_exchange() is None
