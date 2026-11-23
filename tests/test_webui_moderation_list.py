"""The dashboard's list of cases: filters, the JSON it returns, an outage (T01)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import src.webui.routes.moderation as moderation_routes
from features.moderation import Infraction, ModerationRepository
from src.core.errors import DatabaseError

START = datetime(2026, 1, 1, tzinfo=UTC)


class Ctx:
    bot = None
    oauth = SimpleNamespace(is_developer=lambda session: False)

    def __init__(self):
        self.checked: list[str] = []

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        self.checked.append(guild_id)
        return SimpleNamespace(username="alice", user_id="7", guilds=[])


def list_endpoint(ctx):
    router = moderation_routes.create_router(ctx)
    return next(r.endpoint for r in router.routes if r.path.endswith("/infractions"))


@pytest.mark.mongo
async def test_cases_are_listed_with_their_filters(mongo):
    repo = ModerationRepository(mongo.guild_id)
    for case_id, user, kind, active in [
        (1, "5", "warn", True),
        (2, "5", "ban", False),
        (3, "6", "warn", True),
    ]:
        await repo.add(
            Infraction(
                id=case_id,
                guild_id=mongo.guild_id,
                user_id=user,
                moderator_id="1",
                type=kind,
                active=active,
                reason="r",
                duration_seconds=None,
                created_at=START + timedelta(minutes=case_id),
                expires_at=START + timedelta(hours=1) if kind == "ban" else None,
            )
        )
    ctx = Ctx()
    endpoint = list_endpoint(ctx)

    async def ids(**filters):
        args = {"user_id": None, "type": None, "active": None, **filters}
        response = await endpoint(None, mongo.guild_id, **args)
        return [c["id"] for c in json.loads(response.body)["infractions"]]

    assert await ids() == [3, 2, 1]
    assert await ids(user_id="5") == [2, 1]
    assert await ids(type="warn") == [3, 1]
    assert await ids(active="true") == [3, 1]
    assert await ids(active="0") == [2]
    assert await ids(active="anything") == [3, 2, 1]  # not a filter
    assert ctx.checked and set(ctx.checked) == {mongo.guild_id}

    response = await endpoint(None, mongo.guild_id, user_id=None, type="ban", active=None)
    (case,) = json.loads(response.body)["infractions"]
    assert case["created_at"] == (START + timedelta(minutes=2)).isoformat()
    assert case["expires_at"] == (START + timedelta(hours=1)).isoformat()
    assert case["active"] is False


async def test_an_outage_is_a_503_not_an_empty_list(monkeypatch):
    class Down:
        def __init__(self, guild_id):
            pass

        async def list(self, **kwargs):
            raise DatabaseError("down")

    monkeypatch.setattr(moderation_routes, "ModerationRepository", Down)
    with pytest.raises(DatabaseError):  # the app answers it 503 (src/webui/errors.py)
        await list_endpoint(Ctx())(None, "1", user_id=None, type=None, active=None)
