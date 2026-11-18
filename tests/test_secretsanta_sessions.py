"""/secretsanta creer, modifier and participants: the date, the parcel option, the counts (P2)."""

from datetime import datetime

import pytest
from discord_fakes import (
    FakeChannel,
    FakeCtx,
    FakeGuild,
    FakeMember,
    FakeMessage,
    bare_extension,
    run,
)
from interactions import Permissions
from santa_fakes import MemorySanta

import extensions.secretsanta.sessions as sessions_module
from extensions.secretsanta import SecretSantaExtension
from features.secretsanta import SecretSantaSession
from features.secretsanta.relay import RelayLimiter
from src.core import clock


@pytest.fixture
def setup(store_config, monkeypatch):
    store_config({"servers": {"1": {"moduleSecretSanta": {"enabled": True}}}})
    monkeypatch.setattr(
        clock, "local_now", lambda: datetime(2026, 11, 17, 21, tzinfo=clock.LOCAL_TZ)
    )
    edits = []

    async def edit(message, **kwargs):
        edits.append(kwargs)

    monkeypatch.setattr(sessions_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    channel.messages[77] = FakeMessage(77, channel=channel)
    members = {uid: guild.add_member(FakeMember(uid, f"m{uid}")) for uid in (3, 4, 5)}
    repo = MemorySanta()

    def call(command, uid, **options):
        ctx = FakeCtx(members[uid], channel)
        ext = bare_extension(
            SecretSantaExtension, ctx.bot, repository=repo, relay_limiter=RelayLimiter()
        )
        return run(command, ext, ctx, **options), ctx

    return repo, members, call, edits


def session(**fields):
    return SecretSantaSession(
        context_id="guild_1",
        channel_id=10,
        message_id=77,
        created_by=3,
        participants=[3, 4, 5],
        **fields,
    )


async def test_create_reads_the_date_and_defaults_to_an_in_person_exchange(setup):
    repo, _, call, _ = setup
    coro, ctx = call(SecretSantaExtension.create_session, 3, budget="20 €", exchange="24/12")
    await coro
    assert repo.session.exchange_date == "2026-12-24" and repo.session.shipping is False
    text = ctx.replies[-1]["embed"].description
    assert "jeudi 24 décembre 2026" in text and "20 €" in text and "colis" not in text
    labels = [b.label for row in ctx.replies[-1]["components"] for b in row.components]
    assert labels == ["Participer 🎁", "📝 Ma liste", "Se retirer"]


async def test_create_with_parcels(setup):
    repo, _, call, _ = setup
    coro, ctx = call(SecretSantaExtension.create_session, 3, shipping=True)
    await coro
    assert repo.session.shipping is True and repo.session.exchange_date is None
    assert "📦 **Cadeaux envoyés par colis**" in ctx.replies[-1]["embed"].description


async def test_an_unreadable_date_creates_nothing(setup):
    repo, _, call, _ = setup
    coro, ctx = call(SecretSantaExtension.create_session, 3, exchange="à Noël")
    await coro
    assert repo.session is None and "Date non reconnue" in ctx.last


async def test_the_organiser_changes_the_session(setup):
    repo, _, call, edits = setup
    repo.session = session(exchange_date="2026-12-24", reminded=True)
    await repo.set_wishlist("guild_1", 4, "un livre", None)
    coro, ctx = call(
        SecretSantaExtension.modify_session, 3, budget="30 €", exchange="31/12", shipping=True
    )
    await coro
    s = repo.session
    assert (s.budget, s.exchange_date, s.shipping, s.reminded) == (
        "30 €",
        "2026-12-31",
        True,
        False,
    )
    assert clock.to_local(repo.expiries[4]).date().isoformat() == "2027-01-30"
    assert "jeudi 31 décembre 2026" in edits[-1]["embed"].description
    assert ctx.replies[-1]["embed"].title == "🎅 Session modifiée"


async def test_after_the_draw_modifier_points_to_remind(setup):
    repo, _, call, edits = setup
    repo.session = session(is_drawn=True)
    coro, ctx = call(SecretSantaExtension.modify_session, 3, shipping=True)
    await coro
    assert repo.session.shipping and edits == []  # the drawn announcement stays
    assert "/secretsanta relancer" in ctx.replies[-1]["embed"].description


@pytest.mark.parametrize(
    ("uid", "options", "error"),
    [
        (4, {"budget": "1 €"}, "Seul le créateur"),
        (3, {}, "au moins un changement"),
        (3, {"exchange": "hier"}, "Date non reconnue"),
    ],
)
async def test_modifier_refusals(setup, uid, options, error):
    repo, _, call, _ = setup
    repo.session = session(budget="20 €")
    coro, ctx = call(SecretSantaExtension.modify_session, uid, **options)
    await coro
    assert error in ctx.last and repo.session.budget == "20 €"


async def test_an_admin_may_modify(setup):
    repo, members, call, _ = setup
    repo.session = session()
    members[5].guild_permissions = Permissions.ADMINISTRATOR
    coro, _ = call(SecretSantaExtension.modify_session, 5, budget="15 €")
    await coro
    assert repo.session.budget == "15 €"


async def test_participants_counts_the_wish_lists_before_the_draw(setup):
    repo, _, call, _ = setup
    repo.session = session()
    await repo.set_wishlist("guild_1", 4, "un livre", None)
    await repo.set_wishlist("guild_1", 9, "parti depuis", None)  # not a participant now
    coro, ctx = call(SecretSantaExtension.list_participants, 4)
    await coro
    fields = {f.name: f.value for f in ctx.replies[-1]["embed"].fields}
    assert fields["📝 Listes d'envies"] == "1/3"


async def test_participants_counts_parcels_but_never_names_them(setup):
    repo, _, call, _ = setup
    repo.session = session(is_drawn=True, shipping=True)
    await repo.mark_gift("guild_1", 3, "sent_at", None)
    await repo.mark_gift("guild_1", 4, "sent_at", None)
    await repo.mark_gift("guild_1", 5, "received_at", None)
    coro, ctx = call(SecretSantaExtension.list_participants, 3)
    await coro
    fields = {f.name: f.value for f in ctx.replies[-1]["embed"].fields}
    assert fields["📦 Colis"] == "Envoyés : 2/3 · Reçus : 1/3"


async def test_an_in_person_drawn_session_shows_no_parcel_counts(setup):
    repo, _, call, _ = setup
    repo.session = session(is_drawn=True)
    coro, ctx = call(SecretSantaExtension.list_participants, 3)
    await coro
    assert [f.name for f in ctx.replies[-1]["embed"].fields] == []


async def test_cancel_drops_the_entries_and_the_relay_counters(setup):
    repo, _, call, _ = setup
    repo.session = session()
    await repo.set_wishlist("guild_1", 4, "un livre", None)
    coro, _ = call(SecretSantaExtension.cancel_session, 3)
    await coro
    assert repo.session is None and repo.people == {}
