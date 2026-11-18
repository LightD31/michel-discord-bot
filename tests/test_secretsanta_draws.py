"""/secretsanta tirage: who may run it, one draw per session, and the DMs (T04, P2)."""

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
from interactions import Embed, Permissions
from santa_fakes import MemorySanta

import src.discord_ext.secretsanta as draw_module
from extensions.secretsanta import SecretSantaExtension
from features.secretsanta import SecretSantaSession


@pytest.fixture
def setup(monkeypatch, tmp_path, store_config):
    store_config({"servers": {"1": {"moduleSecretSanta": {"enabled": True}}}})
    monkeypatch.setattr(draw_module, "DATA_DIR", tmp_path)

    async def edit(message, **kwargs):
        await message.edit(**kwargs)

    monkeypatch.setattr(draw_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    members = [guild.add_member(FakeMember(uid, f"m{uid}")) for uid in (3, 4, 5, 6)]
    announcement = FakeMessage(77, embeds=[Embed(title="🎅")], channel=channel)
    channel.messages[77] = announcement
    session = SecretSantaSession(
        context_id="guild_1",
        channel_id=10,
        message_id=77,
        created_by=3,
        participants=[m.id for m in members],
    )
    repo = MemorySanta(session)
    ctx = FakeCtx(members[0], channel)  # the creator
    ext = bare_extension(SecretSantaExtension, ctx.bot, repository=repo)
    return ext, ctx, repo, members, tmp_path


async def test_the_creator_draws_and_everyone_gets_their_dm(setup):
    ext, ctx, repo, members, tmp_path = setup
    await run(SecretSantaExtension.draw, ext, ctx)
    assert repo.session.is_drawn
    givers = [g for g, _ in repo.results]
    receivers = [r for _, r in repo.results]
    assert sorted(givers) == sorted(receivers) == [3, 4, 5, 6]
    assert all(g != r for g, r in repo.results)
    for member in members:
        assert len(member.dms) == 1  # one DM each (an embed, no text)
    assert list(tmp_path.glob("draw_guild_1_*.txt"))
    assert ctx.replies[-1]["embed"].title == "🎅 Tirage effectué"


async def test_a_member_who_is_neither_creator_nor_admin_cannot_draw(setup):
    ext, ctx, repo, members, _ = setup
    other = FakeCtx(members[1], ctx.channel)
    await run(SecretSantaExtension.draw, ext, other)
    assert "Seul le créateur" in other.last
    assert not repo.session.is_drawn and repo.results is None


async def test_an_admin_may_draw(setup):
    ext, ctx, repo, members, _ = setup
    members[1].guild_permissions = Permissions.ADMINISTRATOR
    await run(SecretSantaExtension.draw, ext, FakeCtx(members[1], ctx.channel))
    assert repo.session.is_drawn


async def test_a_second_draw_is_refused(setup):
    ext, ctx, repo, members, _ = setup
    await run(SecretSantaExtension.draw, ext, ctx)
    first = list(repo.results)
    await run(SecretSantaExtension.draw, ext, ctx)
    assert "déjà été effectué" in ctx.last
    assert repo.results == first
    assert all(len(m.dms) == 1 for m in members)


async def test_a_draw_lost_to_a_concurrent_one_sends_nothing(setup):
    ext, ctx, repo, members, _ = setup
    repo.claim_draw = _never  # the other draw claimed the session first
    await run(SecretSantaExtension.draw, ext, ctx)
    assert "déjà été effectué" in ctx.last
    assert all(m.dms == [] for m in members)


async def _never(context_id):
    return False


async def test_closed_dms_are_reported_to_whoever_drew(setup):
    ext, ctx, repo, members, _ = setup
    members[2].dms_closed = True
    await run(SecretSantaExtension.draw, ext, ctx)
    assert "<@5>" in ctx.replies[-1]["embed"].description


async def test_a_lonely_participant_is_not_enough(setup):
    ext, ctx, repo, _, _ = setup
    repo.session.participants = [3]
    await run(SecretSantaExtension.draw, ext, ctx)
    assert "au moins 2 participants" in ctx.last


async def test_impossible_constraints_are_explained(setup):
    ext, ctx, repo, _, _ = setup
    repo.session.participants = [3, 4]
    repo.banned = [(3, 4), (4, 3)]
    await run(SecretSantaExtension.draw, ext, ctx)
    assert "Impossible de générer" in ctx.last and "sous_groupes" in ctx.last
    assert not repo.session.is_drawn


# ── The draw DM (P2) ───────────────────────────────────────────────────────


def _labels(message):
    return [b.label for row in message.components or [] for b in row.components]


def _dm_for(members, repo, receiver_id):
    giver_id = next(g for g, r in repo.results if r == receiver_id)
    return next(m for m in members if m.id == giver_id).dm_messages[0]


async def test_the_dm_carries_the_wish_list_budget_and_date(setup):
    ext, ctx, repo, members, _ = setup
    repo.session.budget, repo.session.exchange_date = "20 €", "2026-12-24"
    await repo.set_wishlist("guild_1", 4, "Un bonnet rouge", None)
    await run(SecretSantaExtension.draw, ext, ctx)

    text = _dm_for(members, repo, 4).embeds[0].description
    assert "<@4>" in text and "Un bonnet rouge" in text
    assert "20 €" in text and "jeudi 24 décembre 2026" in text
    without = _dm_for(members, repo, 5).embeds[0].description
    assert "Pas de liste d'envies" in without and "Écris-lui anonymement" in without


async def test_an_in_person_exchange_has_no_parcel_buttons(setup):
    ext, ctx, repo, members, _ = setup
    await run(SecretSantaExtension.draw, ext, ctx)
    for member in members:
        assert _labels(member.dm_messages[0]) == ["✉️ Écrire anonymement"]
        assert "colis" not in member.dm_messages[0].embeds[0].description


async def test_a_parcel_exchange_gets_the_sent_and_received_buttons(setup):
    ext, ctx, repo, members, _ = setup
    repo.session.shipping = True
    await run(SecretSantaExtension.draw, ext, ctx)
    message = members[0].dm_messages[0]
    assert _labels(message) == ["✉️ Écrire anonymement", "📦 Cadeau envoyé", "🎁 Cadeau reçu"]
    assert "📦 Cadeau envoyé" in message.embeds[0].description
    ids = [b.custom_id for row in message.components for b in row.components]
    assert ids == [
        "secretsanta_write:guild_1",
        "secretsanta_sent:guild_1",
        "secretsanta_received:guild_1",
    ]


async def test_without_anonymous_messages_there_is_no_write_button(setup, store_config):
    store_config(
        {"servers": {"1": {"moduleSecretSanta": {"enabled": True, "anonymousMessages": False}}}}
    )
    ext, ctx, repo, members, _ = setup
    await run(SecretSantaExtension.draw, ext, ctx)
    assert members[0].dm_messages[0].components == []
    assert "Écris-lui" not in members[0].dm_messages[0].embeds[0].description


async def test_remind_sends_the_dm_again_with_its_buttons(setup):
    ext, ctx, repo, members, _ = setup
    repo.session.shipping = True
    await run(SecretSantaExtension.draw, ext, ctx)
    receiver = next(r for g, r in repo.results if g == 3)
    await repo.set_wishlist("guild_1", receiver, "Du thé", None)

    await run(SecretSantaExtension.remind_assignment, ext, FakeCtx(members[0], ctx.channel))
    again = members[0].dm_messages[-1]
    assert again.embeds[0].title == "🎅 Rappel - Père Noël Secret"
    assert "Du thé" in again.embeds[0].description
    assert "📦 Cadeau envoyé" in _labels(again)
