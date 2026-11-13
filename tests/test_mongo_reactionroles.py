"""Role menus against a real MongoDB, and a MongoDB outage that isn't "menu not found" (B17)."""

from datetime import UTC, datetime

import pymongo.errors
import pytest

from features.reactionroles.models import RoleMenu, RoleMenuEntry
from features.reactionroles.repository import ReactionRolesRepository
from src.core.errors import DatabaseError


def menu(guild_id: str) -> RoleMenu:
    return RoleMenu(
        guild_id=guild_id,
        channel_id="10",
        title="Rôles",
        entries=[RoleMenuEntry(role_id="5", emoji="🎮", label="Jeux")],
        created_by="7",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.mongo
async def test_a_menu_is_stored_edited_and_deleted(mongo):
    repo = ReactionRolesRepository(mongo.guild_id)
    menu_id = await repo.add(menu(mongo.guild_id))
    assert (await repo.get(menu_id)).title == "Rôles"
    assert await repo.update(menu_id, title="Jeux") == 1
    assert [m.title for m in await repo.list()] == ["Jeux"]
    assert await repo.delete(menu_id) == 1
    assert await repo.get(menu_id) is None


@pytest.mark.mongo
async def test_a_malformed_id_is_just_not_found(mongo):
    repo = ReactionRolesRepository(mongo.guild_id)
    assert await repo.get("not-an-id") is None
    assert await repo.update("not-an-id", title="x") == 0
    assert await repo.delete("not-an-id") == 0


async def test_an_outage_is_a_database_error_not_a_missing_menu(monkeypatch):
    class Down:
        async def find_one(self, *args, **kwargs):
            raise pymongo.errors.ServerSelectionTimeoutError("no server")

    repo = ReactionRolesRepository("1")
    monkeypatch.setattr(repo, "_col", lambda: Down())
    with pytest.raises(DatabaseError):
        await repo.get("a" * 24)
