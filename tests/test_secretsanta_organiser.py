"""Only the organiser of a Secret Santa (or an administrator) steers its draw.

A member who could add exclusions would leave the draw a single choice for
themselves; one who could re-create a drawn session would wipe its results.
"""

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run
from interactions import Permissions

from extensions.secretsanta import SecretSantaExtension
from extensions.secretsanta._common import ORGANISER_ONLY
from features.secretsanta import SecretSantaSession


class MemorySanta:
    def __init__(self, session=None):
        self.session = session
        self.banned: list[tuple[int, int]] = []
        self.saved = []

    async def get_session(self, context_id):
        return self.session

    async def read_banned_pairs(self, context_id):
        return list(self.banned)

    async def write_banned_pairs(self, context_id, pairs):
        self.banned = list(pairs)

    async def save_session(self, session):
        self.saved.append(session)
        self.session = session


@pytest.fixture
def setup(store_config):
    store_config({"servers": {"1": {"moduleSecretSanta": {"enabled": True}}}})
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    creator = guild.add_member(FakeMember(3, "orga"))
    member = guild.add_member(FakeMember(4, "lambda"))
    admin = guild.add_member(FakeMember(5, "admin", permissions=Permissions.ADMINISTRATOR))
    target = guild.add_member(FakeMember(6, "cible"))
    repo = MemorySanta(
        SecretSantaSession(context_id="guild_1", channel_id=10, created_by=3, participants=[3, 4])
    )

    def ctx_for(author):
        ctx = FakeCtx(author, channel)
        return bare_extension(SecretSantaExtension, ctx.bot, repository=repo), ctx

    return ctx_for, repo, creator, member, admin, target


async def test_a_member_cannot_ban_or_unban_pairs(setup):
    ctx_for, repo, _, member, _, target = setup
    ext, ctx = ctx_for(member)
    await run(SecretSantaExtension.ban_pair, ext, ctx, member, target)
    assert repo.banned == [] and ORGANISER_ONLY in ctx.last
    repo.banned = [(3, 6)]
    await run(SecretSantaExtension.unban_pair, ext, ctx, ctx.author, target)
    await run(SecretSantaExtension.list_bans, ext, ctx)
    assert repo.banned == [(3, 6)]
    assert all(ORGANISER_ONLY in text for text in ctx.texts)


@pytest.mark.parametrize("who", ["creator", "admin"])
async def test_the_creator_or_an_admin_bans_pairs(setup, who):
    ctx_for, repo, creator, member, admin, target = setup
    ext, ctx = ctx_for(creator if who == "creator" else admin)
    await run(SecretSantaExtension.ban_pair, ext, ctx, member, target)
    assert repo.banned == [(4, 6)]


async def test_exclusions_are_frozen_once_drawn(setup):
    ctx_for, repo, creator, member, _, target = setup
    repo.session.is_drawn = True
    ext, ctx = ctx_for(creator)
    await run(SecretSantaExtension.ban_pair, ext, ctx, member, target)
    assert repo.banned == [] and "tirage a déjà eu lieu" in ctx.last


async def test_without_a_session_only_an_admin_manages_exclusions(setup):
    ctx_for, repo, creator, member, admin, target = setup
    repo.session = None
    ext, ctx = ctx_for(creator)
    await run(SecretSantaExtension.ban_pair, ext, ctx, member, target)
    assert repo.banned == []
    ext, ctx = ctx_for(admin)
    await run(SecretSantaExtension.ban_pair, ext, ctx, member, target)
    assert repo.banned == [(4, 6)]


async def test_a_member_cannot_recreate_over_a_drawn_session(setup):
    ctx_for, repo, creator, member, _, _ = setup
    repo.session.is_drawn = True
    ext, ctx = ctx_for(member)
    await run(SecretSantaExtension.create_session, ext, ctx)
    assert repo.saved == [] and repo.session.created_by == 3
    ext, ctx = ctx_for(creator)
    await run(SecretSantaExtension.create_session, ext, ctx)
    assert repo.saved and repo.session.created_by == 3 and not repo.session.is_drawn
