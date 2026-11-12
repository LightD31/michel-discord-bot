"""Button polls: creation, public / anonymous / ranked votes, and their close (T04)."""

from datetime import timedelta

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

import extensions.polls as polls_module
import extensions.polls.buttons as buttons_module
from extensions.polls import PollsExtension
from src.core import clock


class MemoryPolls:
    def __init__(self):
        self.items = {}

    async def add(self, poll):
        pid = f"{len(self.items) + 1:024x}"
        poll.id = pid
        self.items[pid] = poll
        return pid

    async def get_by_message(self, message_id):
        return next((p for p in self.items.values() if p.message_id == str(message_id)), None)

    async def set_vote(self, poll_id, user_id, ranking):
        self.items[poll_id].votes[user_id] = list(ranking)

    async def clear_vote(self, poll_id, user_id):
        self.items[poll_id].votes.pop(user_id, None)

    async def list_due(self, now):
        return [
            p for p in self.items.values() if p.closes_at and p.closes_at <= now and not p.closed
        ]

    async def mark_closed(self, poll_id):
        self.items[poll_id].closed = True


@pytest.fixture
def setup(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleUtils": {"enabled": True}}}})

    async def edit(message, **kwargs):
        await message.edit(**kwargs)

    monkeypatch.setattr(polls_module, "edit_message_if_changed", edit)
    monkeypatch.setattr(buttons_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    author = guild.add_member(FakeMember(3, "alice"))
    repo = MemoryPolls()
    ctx = FakeCtx(author, channel)
    ext = bare_extension(PollsExtension, ctx.bot, _poll_repo=lambda guild_id: repo)
    return ext, ctx, repo, guild, channel


async def create(ext, ctx, command, options="Pizza;Sushi;Tacos", **kw):
    await run(command, ext, ctx, question="Ce soir ?", options=options, **kw)
    return ctx.channel.messages[max(ctx.channel.messages)]


def voter(guild, channel, message, uid, custom_id):
    member = guild.add_member(FakeMember(uid, f"m{uid}"))
    return FakeCtx(member, channel, message=message, custom_id=custom_id)


async def vote(ext, guild, channel, message, poll, uid, option):
    ctx = voter(guild, channel, message, uid, f"poll_vote:{poll.id}:{option}")
    await run(PollsExtension.on_poll_vote, ext, ctx)
    return ctx


def results(message):
    (embed,) = message.embeds
    return next(f.value for f in embed.fields if f.name.startswith("Résultats"))


async def test_an_anonymous_poll_is_stored_with_its_buttons(setup):
    ext, ctx, repo, _, _ = setup
    message = await create(ext, ctx, PollsExtension.poll_anonyme, duration="1h")
    (poll,) = repo.items.values()
    assert poll.mode == "anonymous" and poll.options == ["Pizza", "Sushi", "Tacos"]
    assert poll.closes_at - clock.now() == pytest.approx(
        timedelta(hours=1), abs=timedelta(seconds=5)
    )
    components = message.edits[-1]["components"]
    assert [b.custom_id for b in components[0].components] == [
        f"poll_vote:{poll.id}:{i}" for i in range(3)
    ]


@pytest.mark.parametrize("options", ["Seule", "A;" * 12])
async def test_a_poll_needs_two_to_ten_options(setup, options):
    ext, ctx, repo, _, _ = setup
    await run(PollsExtension.poll_anonyme, ext, ctx, question="?", options=options)
    assert "entre 2 et 10 options" in ctx.last
    assert repo.items == {}


async def test_a_vote_is_counted_and_can_be_changed(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx, PollsExtension.poll_anonyme)
    (poll,) = repo.items.values()
    first = await vote(ext, guild, channel, message, poll, 7, 0)
    assert "Pizza" in first.last and "(anonyme)" in first.last
    await vote(ext, guild, channel, message, poll, 7, 1)
    assert poll.votes == {"7": [1]}
    assert "1 votant(s)" in results(message)


async def test_a_ranked_vote_builds_a_ranking_and_can_be_reset(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx, PollsExtension.poll_classement)
    (poll,) = repo.items.values()
    await vote(ext, guild, channel, message, poll, 7, 2)
    second = await vote(ext, guild, channel, message, poll, 7, 0)
    assert poll.votes["7"] == [2, 0]
    assert "#1 Tacos → #2 Pizza" in second.last
    again = await vote(ext, guild, channel, message, poll, 7, 2)
    assert "déjà à la position #1" in again.last

    reset = voter(guild, channel, message, 7, f"poll_reset:{poll.id}")
    await run(PollsExtension.on_poll_reset, ext, reset)
    assert "7" not in poll.votes and reset.last == "Classement réinitialisé."


async def test_a_vote_on_another_poll_id_is_refused(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx, PollsExtension.poll_anonyme)
    ctx_vote = voter(guild, channel, message, 7, "poll_vote:ffff:0")
    await run(PollsExtension.on_poll_vote, ext, ctx_vote)
    assert ctx_vote.last == "Sondage introuvable."


async def test_the_sweep_closes_a_due_poll_and_disables_its_buttons(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx, PollsExtension.poll_anonyme, duration="1h")
    (poll,) = repo.items.values()
    await vote(ext, guild, channel, message, poll, 7, 0)
    poll.closes_at = clock.now() - timedelta(minutes=1)

    await PollsExtension.check_closing_polls.callback(ext)

    assert poll.closed
    assert message.embeds[0].title.startswith("🔒")
    assert all(b.disabled for row in message.edits[-1]["components"] for b in row.components)
    late = await vote(ext, guild, channel, message, poll, 8, 1)
    assert late.last == "Ce sondage est fermé."
