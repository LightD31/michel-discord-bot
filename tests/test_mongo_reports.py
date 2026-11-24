"""Reports of a message against a real MongoDB: one card per message, handled once."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from features.moderation import ReportRepository
from features.moderation.reports import KEEP_FOR
from features.userdata import NOBODY, UserDataRepository

pytestmark = pytest.mark.mongo

NOW = datetime(2026, 11, 18, 20, tzinfo=UTC)
AUTHOR = "333333333333333333"
ME = "111111111111111111"
OTHER = "222222222222222222"


def report(repo, reporter, *, message="500", reason="spam", minutes=0):
    return repo.add(
        message_id=message,
        channel_id="10",
        author_id=AUTHOR,
        reporter_id=reporter,
        reason=reason,
        now=NOW + timedelta(minutes=minutes),
    )


async def test_every_report_of_a_message_lands_on_one_document(mongo):
    repo = ReportRepository(mongo.guild_id)
    await repo.ensure_indexes()
    first = await report(repo, ME, reason="insulte")
    assert first is not None and first.is_open and len(first.reporters) == 1
    assert first.expires_at == NOW + KEEP_FOR
    second = await report(repo, OTHER, minutes=5)
    assert second is not None
    assert [r.user_id for r in second.reporters] == [ME, OTHER]
    assert second.created_at == NOW  # the first report's date stays
    # The same member twice: refused, nothing added.
    assert await report(repo, ME, reason="encore") is None
    stored = await repo.get("500")
    assert stored is not None and [r.reason for r in stored.reporters] == ["insulte", "spam"]
    assert await repo.count_against(AUTHOR) == 1


async def test_concurrent_reports_by_different_members_are_all_kept(mongo):
    """Two first reports of a message at once: both upserts insert, and the loser's
    duplicate key was taken for "already reported", losing its report. Each round
    is a fresh message, so every round races on the insert."""
    repo = ReportRepository(mongo.guild_id)
    members = [f"4{i:017d}" for i in range(1, 11)]
    for round_ in range(20):
        message = str(600 + round_)
        results = await asyncio.gather(*(report(repo, m, message=message) for m in members))
        assert all(r is not None for r in results), f"a report was lost on round {round_}"
        stored = await repo.get(message)
        assert sorted(r.user_id for r in stored.reporters) == sorted(members)


async def test_a_member_racing_their_own_report_is_counted_once(mongo):
    repo = ReportRepository(mongo.guild_id)
    results = await asyncio.gather(*(report(repo, ME, message="700") for _ in range(5)))
    assert sum(r is not None for r in results) == 1
    assert [r.user_id for r in (await repo.get("700")).reporters] == [ME]


async def test_a_report_is_handled_once(mongo):
    repo = ReportRepository(mongo.guild_id)
    await report(repo, ME)
    claims = await asyncio.gather(
        repo.claim("500", "warned", "7", NOW), repo.claim("500", "dismissed", "8", NOW)
    )
    won = [c for c in claims if c is not None]
    assert len(won) == 1 and won[0].handled_by in ("7", "8")
    # A failed action reopens it, but only from the status it was claimed for.
    await repo.reopen("500", "deleted")
    assert not (await repo.get("500")).is_open
    await repo.reopen("500", won[0].status)
    reopened = await repo.get("500")
    assert reopened.is_open and reopened.handled_by is None
    await repo.set_card("500", "20", "900")
    await repo.set_case("500", 12)
    stored = await repo.get("500")
    assert (stored.card_channel_id, stored.card_message_id, stored.case_id) == ("20", "900", 12)


async def test_reports_expire_after_a_year(mongo):
    repo = ReportRepository(mongo.guild_id)
    await repo.ensure_indexes()
    indexes = await mongo.guild_collection("reports").index_information()
    assert indexes["expires_ttl"]["expireAfterSeconds"] == 0


async def test_a_reporter_is_anonymised_and_the_author_kept(mongo):
    repo = ReportRepository(mongo.guild_id)
    await report(repo, ME, reason="insulte")
    await report(repo, OTHER)
    data = await UserDataRepository(ME).export([mongo.guild_id])
    (mine,) = data["guilds"][mongo.guild_id]["reports"]
    assert [r["user_id"] for r in mine["reporters"]] == [ME]  # their own entry, not OTHER's
    authored = await UserDataRepository(AUTHOR).export([mongo.guild_id])
    (theirs,) = authored["guilds"][mongo.guild_id]["reports"]
    assert "reporters" not in theirs  # the author never learns who reported them

    forgotten = await UserDataRepository(ME).forget([mongo.guild_id])
    stored = await repo.get("500")
    assert [r.user_id for r in stored.reporters] == [NOBODY, OTHER]
    assert stored.reporters[0].reason == "insulte"
    assert forgotten.anonymised[f"guild_{mongo.guild_id}.reports"] == 1
    kept = await UserDataRepository(AUTHOR).forget([mongo.guild_id])
    assert f"guild_{mongo.guild_id}.reports" in kept.kept
    assert await repo.count_against(AUTHOR) == 1


async def test_the_dashboard_lists_the_reports_for_moderators_only(mongo):
    """P15: the reports on the Modération page, newest first, open ones on their own."""
    import json
    from types import SimpleNamespace

    from fastapi import HTTPException

    import src.webui.routes.moderation as moderation_routes
    from src.core.permissions import MANAGE_GUILD, MODERATE_MEMBERS
    from src.webui.guild_access import GuildAccess

    repo = ReportRepository(mongo.guild_id)
    await report(repo, ME, message="500", minutes=0)
    await report(repo, OTHER, message="500", reason="insulte", minutes=1)
    await report(repo, ME, message="501", minutes=5)
    await repo.set_card("500", "90", "900")
    await repo.claim("501", "dismissed", OTHER, NOW + timedelta(minutes=6))

    class Ctx:
        bot = object()
        oauth = SimpleNamespace(is_developer=lambda session: False)
        permissions = MODERATE_MEMBERS

        async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
            return SimpleNamespace(username="alice", user_id="7", guilds=[])

        async def guild_access(self, session, guild_id):  # noqa: ARG002
            return GuildAccess(is_member=True, permissions=self.permissions)

    ctx = Ctx()
    router = moderation_routes.create_router(ctx)
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith("/reports"))

    everything = json.loads((await endpoint(None, mongo.guild_id, status=None)).body)["reports"]
    assert [r["message_id"] for r in everything] == ["501", "500"]
    assert everything[0]["status"] == "dismissed" and everything[0]["handled_by"] == OTHER
    first = everything[1]
    assert [r["reason"] for r in first["reporters"]] == ["spam", "insulte"]
    assert first["card_url"] == f"https://discord.com/channels/{mongo.guild_id}/90/900"
    assert first["message_url"] == f"https://discord.com/channels/{mongo.guild_id}/10/500"
    opened = json.loads((await endpoint(None, mongo.guild_id, status="open")).body)["reports"]
    assert [r["message_id"] for r in opened] == ["500"]

    ctx.permissions = MANAGE_GUILD  # the settings permission: not enough to see who reported
    with pytest.raises(HTTPException) as refused:
        await endpoint(None, mongo.guild_id, status=None)
    assert refused.value.status_code == 403
