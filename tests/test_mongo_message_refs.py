"""Where the bot's persistent messages live, against a real MongoDB (see tests/conftest.py)."""

import pytest

from src.core.message_refs import COLLECTION, MessageRef, MessageRefRepository

pytestmark = pytest.mark.mongo


async def test_a_slot_is_saved_read_and_overwritten(mongo):
    repo = MessageRefRepository(mongo.guild_id)
    assert await repo.get("satisfactory") is None

    await repo.save("satisfactory", "10", "100", label="Statut")
    assert await repo.get("satisfactory") == MessageRef("satisfactory", "10", "100", "Statut")

    # A new channel: the same document now points at the new message.
    await repo.save("satisfactory", "20", "200", label="Statut")
    assert await repo.get("satisfactory") == MessageRef("satisfactory", "20", "200", "Statut")
    count = await mongo.manager.get_guild_collection(mongo.guild_id, COLLECTION).count_documents({})
    assert count == 1


async def test_a_slot_is_a_value_not_a_field_path(mongo):
    """Team names are user-chosen: dots and dollars must not reach a field path."""
    repo = MessageRefRepository(mongo.guild_id)
    slot = "vlrgg:team:Team.Liquid $1"

    await repo.save(slot, "10", "100")

    assert (await repo.get(slot)).message_id == "100"
    assert [ref.slot for ref in await repo.list()] == [slot]


async def test_slots_are_listed_in_order_and_deleted_one_by_one(mongo):
    repo = MessageRefRepository(mongo.guild_id)
    await repo.save("speedons:schedule", "10", "1", label="Planning")
    await repo.save("speedons:live", "10", "2", label="Run")
    await repo.save("embedmanager", "11", "3")

    assert [ref.slot for ref in await repo.list()] == [
        "embedmanager",
        "speedons:live",
        "speedons:schedule",
    ]

    removed = await repo.delete("speedons:live")
    assert removed == MessageRef("speedons:live", "10", "2", "Run")
    assert await repo.delete("speedons:live") is None
    assert [ref.slot for ref in await repo.list()] == ["embedmanager", "speedons:schedule"]


async def test_guilds_do_not_see_each_others_messages(mongo):
    await MessageRefRepository(mongo.guild_id).save("satisfactory", "10", "100")
    other = MessageRefRepository(f"{mongo.guild_id}9")
    try:
        assert await other.get("satisfactory") is None
    finally:
        await mongo.manager.client.drop_database(f"guild_{mongo.guild_id}9")
