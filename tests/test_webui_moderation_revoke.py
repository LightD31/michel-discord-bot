"""Revoking a case from the dashboard is a moderation act, signed by its author (S10)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.webui.routes.moderation as moderation_routes
from features.moderation import Infraction
from src.core import clock
from src.core.permissions import MANAGE_GUILD, MODERATE_MEMBERS
from src.discord_ext.mentions import NO_PINGS
from src.webui.guild_access import GuildAccess

GUILD = "1"
EDITOR = "7"


class Repo:
    def __init__(self, target: str):
        self.case = Infraction(
            id=3,
            guild_id=GUILD,
            user_id=target,
            moderator_id="9",
            type="warn",
            created_at=clock.now(),
        )

    async def get(self, case_id):
        return self.case if case_id == self.case.id else None

    async def set_active(self, case_id, active):
        self.case.active = active


class Channel:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))


class Ctx:
    def __init__(self, permissions: int):
        self.bot = object()
        self.oauth = SimpleNamespace(is_developer=lambda session: False)
        self.permissions = permissions

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        return SimpleNamespace(username="alice", user_id=EDITOR, guilds=[])

    async def guild_access(self, session, guild_id):  # noqa: ARG002
        return GuildAccess(is_member=True, permissions=self.permissions)

    async def call_bot(self, func, *args, what: str, **kwargs):  # noqa: ARG002
        return await func(*args, **kwargs)

    def get_full_config(self):
        return {"servers": {GUILD: {"moduleModeration": {"modLogChannelId": "10"}}}}


@pytest.fixture
def revoke(monkeypatch):
    channel = Channel()

    async def fetch_guild_channel(bot, guild_id, channel_id, **kwargs):
        return channel

    monkeypatch.setattr(moderation_routes, "fetch_guild_channel", fetch_guild_channel)

    def call(permissions: int, target: str = "5"):
        repo = Repo(target)
        monkeypatch.setattr(moderation_routes, "ModerationRepository", lambda guild_id: repo)
        router = moderation_routes.create_router(Ctx(permissions))
        endpoint = next(r.endpoint for r in router.routes if "DELETE" in r.methods)
        return asyncio.run(endpoint(None, GUILD, 3)), repo

    return call, channel


def test_managing_the_server_is_not_enough(revoke):
    call, channel = revoke
    with pytest.raises(HTTPException) as exc:
        call(MANAGE_GUILD)
    assert exc.value.status_code == 403 and channel.sent == []


def test_a_moderator_cannot_revoke_their_own_case(revoke):
    call, _ = revoke
    with pytest.raises(HTTPException) as exc:
        call(MANAGE_GUILD | MODERATE_MEMBERS, target=EDITOR)
    assert exc.value.status_code == 403


def test_a_moderator_revokes_and_the_note_names_them(revoke):
    call, channel = revoke
    resp, repo = call(MANAGE_GUILD | MODERATE_MEMBERS)
    assert json.loads(resp.body)["status"] == "ok" and repo.case.active is False
    ((note, kwargs),) = channel.sent
    assert f"<@{EDITOR}>" in note and "alice" in note
    assert kwargs["allowed_mentions"] is NO_PINGS
