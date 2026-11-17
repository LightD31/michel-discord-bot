"""The Participer / Se retirer buttons: each click says what happened (T01, B03).

The repository's conditional updates are tested against MongoDB in
``test_mongo_secretsanta.py``; here an in-memory one plays the same rules, and
the buttons are checked for the answer and the refreshed embed.
"""

import dataclasses

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

import extensions.secretsanta.buttons as buttons_module
from extensions.secretsanta import SecretSantaExtension
from features.secretsanta import SecretSantaSession


class MemorySanta:
    """Conditional updates like the real repository: None when the filter doesn't match."""

    def __init__(self, session):
        self.session = session
        self.lose_race_to_draw = False

    async def get_session(self, context_id):
        return dataclasses.replace(self.session) if self.session else None

    async def add_participant(self, context_id, user_id):
        if self.lose_race_to_draw:
            self.session.is_drawn = True
        s = self.session
        if s is None or s.is_drawn or user_id in s.participants:
            return None
        s.participants = [*s.participants, user_id]
        return dataclasses.replace(s)

    async def remove_participant(self, context_id, user_id):
        s = self.session
        if s is None or s.is_drawn or user_id not in s.participants:
            return None
        s.participants = [p for p in s.participants if p != user_id]
        return dataclasses.replace(s)


@pytest.fixture
def santa(monkeypatch):
    edits = []

    async def edit(message, **kwargs):
        edits.append(kwargs)

    monkeypatch.setattr(buttons_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    creator = guild.add_member(FakeMember(3, "creator"))
    member = guild.add_member(FakeMember(4, "member"))
    session = SecretSantaSession(
        context_id="guild_1", channel_id=10, created_by=3, participants=[3], budget="20 €"
    )
    repo = MemorySanta(session)

    def click(who, action):
        message = FakeMessage(77, channel=channel)
        ctx = FakeCtx(who, channel, message=message, custom_id=f"secretsanta_{action}:guild_1")
        ext = bare_extension(SecretSantaExtension, ctx.bot, repository=repo)
        callback = (
            SecretSantaExtension.on_join_button
            if action == "join"
            else (SecretSantaExtension.on_leave_button)
        )
        return ctx, ext, callback

    return repo, creator, member, click, edits


def answer(ctx):
    (reply,) = ctx.replies
    assert reply["ephemeral"] is True
    return reply["content"]


async def test_joining_adds_the_member_and_refreshes_the_list(santa):
    repo, _, member, click, edits = santa
    ctx, ext, callback = click(member, "join")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Vous êtes inscrit au Père Noël Secret ! 🎁"
    assert repo.session.participants == [3, 4]
    description = edits[-1]["embed"].description
    assert "Participants (2)" in description and "20 €" in description


async def test_joining_twice_or_after_the_draw_changes_nothing(santa):
    repo, creator, member, click, edits = santa
    ctx, ext, callback = click(creator, "join")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Vous participez déjà ! 🎅"

    repo.session.is_drawn = True
    ctx, ext, callback = click(member, "join")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Le tirage a déjà été effectué !"
    assert repo.session.participants == [3] and edits == []


async def test_a_join_that_loses_the_race_with_the_draw_says_so(santa):
    repo, _, member, click, edits = santa
    repo.lose_race_to_draw = True
    ctx, ext, callback = click(member, "join")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Le tirage a déjà été effectué !"
    assert repo.session.participants == [3] and edits == []


async def test_leaving_removes_the_member(santa):
    repo, creator, _, click, edits = santa
    ctx, ext, callback = click(creator, "leave")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Vous avez été retiré du Père Noël Secret."
    assert repo.session.participants == []
    assert "Aucun participant" in edits[-1]["embed"].description


async def test_leaving_without_taking_part_or_after_the_draw_is_refused(santa):
    repo, creator, member, click, _ = santa
    ctx, ext, callback = click(member, "leave")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Vous ne participez pas à cette session."

    repo.session.is_drawn = True
    ctx, ext, callback = click(creator, "leave")
    await run(callback, ext, ctx)
    assert answer(ctx) == "Le tirage a déjà été effectué, vous ne pouvez plus vous retirer."
    assert repo.session.participants == [3]


@pytest.mark.parametrize("action", ["join", "leave"])
async def test_a_deleted_session_is_reported(santa, action):
    repo, _, member, click, _ = santa
    repo.session = None
    ctx, ext, callback = click(member, action)
    await run(callback, ext, ctx)
    assert answer(ctx) == "Cette session n'existe plus."


async def test_in_a_group_dm_the_answer_goes_by_dm(santa):
    _, _, member, click, edits = santa
    ctx, ext, callback = click(member, "join")
    ctx.guild = None
    await run(callback, ext, ctx)
    assert ctx.replies == []  # an ephemeral would only show to the DM's creator
    assert member.dms == ["🎅 **Père Noël Secret**\nVous êtes inscrit au Père Noël Secret ! 🎁"]
    assert edits == []  # no guild embed to refresh
