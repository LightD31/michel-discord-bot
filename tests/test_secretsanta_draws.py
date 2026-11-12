"""/secretsanta draw: who may run it, one draw per session, and the DMs (T04)."""

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

import extensions.secretsanta.draws as draws_module
from extensions.secretsanta import SecretSantaExtension
from features.secretsanta import SecretSantaSession


class MemorySanta:
    def __init__(self, session=None, banned=()):
        self.session = session
        self.banned = list(banned)
        self.results = None

    async def get_session(self, context_id):
        return self.session

    async def read_banned_pairs(self, context_id):
        return self.banned

    async def claim_draw(self, context_id):
        if self.session is None or self.session.is_drawn:
            return False
        self.session.is_drawn = True
        return True

    async def save_draw_results(self, context_id, draw_results):
        self.results = list(draw_results)


@pytest.fixture
def setup(monkeypatch, tmp_path, store_config):
    store_config({"servers": {"1": {"moduleSecretSanta": {"enabled": True}}}})
    monkeypatch.setattr(draws_module, "DATA_DIR", tmp_path)

    async def edit(message, **kwargs):
        await message.edit(**kwargs)

    monkeypatch.setattr(draws_module, "edit_message_if_changed", edit)
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
    assert "Impossible de générer" in ctx.last and "allow_subgroups" in ctx.last
    assert not repo.session.is_drawn
