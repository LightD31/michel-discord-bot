"""``/sondage``: the form, Discord's own public polls, anonymous and ranked votes, the edit."""

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

    def _open(self, poll_id):
        poll = self.items.get(poll_id)
        return None if poll is None or poll.closed else poll

    async def set_vote(self, poll_id, user_id, ranking):
        if poll := self._open(poll_id):
            poll.votes[user_id] = list(ranking)
        return poll

    async def add_to_ranking(self, poll_id, user_id, option):
        if poll := self._open(poll_id):
            ranking = poll.votes.setdefault(user_id, [])
            if option not in ranking:
                ranking.append(option)
        return poll

    async def clear_vote(self, poll_id, user_id):
        if poll := self._open(poll_id):
            poll.votes.pop(user_id, None)
        return poll

    async def list_due(self, now):
        return [
            p for p in self.items.values() if p.closes_at and p.closes_at <= now and not p.closed
        ]

    async def mark_closed(self, poll_id):
        self.items[poll_id].closed = True

    async def edit(self, poll_id, author_id, question, options=None):
        poll = self._open(poll_id)
        if poll is None or poll.author_id != author_id or (options is not None and poll.votes):
            return None
        poll.question = question
        if options is not None:
            poll.options = options
        return poll


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
    ext = bare_extension(
        PollsExtension, ctx.bot, _poll_repo=lambda guild_id: repo, _close_failures={}
    )
    return ext, ctx, repo, guild, channel


async def create(
    ext, ctx, mode="anonymous", options="Pizza\nSushi\nTacos", duration="", multi=False
):
    """``/sondage mode:`` opens its form; the member fills it in."""
    await run(PollsExtension.poll, ext, ctx, mode=mode, multi=multi)
    form = ctx.modals.pop()
    submit = FakeCtx(ctx.author, ctx.channel, bot=ctx.bot, custom_id=form.custom_id)
    await run(
        PollsExtension.on_poll_form,
        ext,
        submit,
        question="Ce soir ?",
        options=options,
        duration=duration,
    )
    ctx.replies.extend(submit.replies)
    return ctx.channel.messages[max(ctx.channel.messages)] if ctx.channel.messages else None


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
    message = await create(ext, ctx, duration="1h")
    (poll,) = repo.items.values()
    assert poll.mode == "anonymous" and poll.options == ["Pizza", "Sushi", "Tacos"]
    assert poll.closes_at - clock.now() == pytest.approx(
        timedelta(hours=1), abs=timedelta(seconds=5)
    )
    components = message.edits[-1]["components"]
    assert [b.custom_id for b in components[0].components] == [
        f"poll_vote:{poll.id}:{i}" for i in range(3)
    ]


async def test_a_poll_names_its_author_but_never_prints_their_id(setup):
    """The footer is public: it named the author *and* printed their Discord id."""
    ext, ctx, _, _, _ = setup
    message = await create(ext, ctx)
    footer = message.embeds[0].footer.text
    assert footer == "Michel · Utilitaires · Sondage de alice"
    assert "3" not in footer


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ("Seule", "au moins 2 options"),
        ("\n".join(f"Option {i}" for i in range(12)), "10 options au plus"),
    ],
)
async def test_a_poll_needs_two_to_ten_options(setup, options, error):
    ext, ctx, repo, _, _ = setup
    await create(ext, ctx, options=options)
    assert error in ctx.last
    assert repo.items == {}


async def test_a_vote_is_counted_and_can_be_changed(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx)
    (poll,) = repo.items.values()
    first = await vote(ext, guild, channel, message, poll, 7, 0)
    assert "Pizza" in first.last and "(anonyme)" in first.last
    await vote(ext, guild, channel, message, poll, 7, 1)
    assert poll.votes == {"7": [1]}
    assert "1 votant(s)" in results(message)


async def test_a_ranked_vote_builds_a_ranking_and_can_be_reset(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx, "ranked")
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
    message = await create(ext, ctx)
    ctx_vote = voter(guild, channel, message, 7, "poll_vote:ffff:0")
    await run(PollsExtension.on_poll_vote, ext, ctx_vote)
    assert ctx_vote.last == "Sondage introuvable."


async def test_the_sweep_closes_a_due_poll_and_disables_its_buttons(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx, duration="1h")
    (poll,) = repo.items.values()
    await vote(ext, guild, channel, message, poll, 7, 0)
    poll.closes_at = clock.now() - timedelta(minutes=1)

    await PollsExtension.check_closing_polls.callback(ext)

    assert poll.closed
    assert message.embeds[0].title.startswith("🔒")
    assert all(b.disabled for row in message.edits[-1]["components"] for b in row.components)
    late = await vote(ext, guild, channel, message, poll, 8, 1)
    assert late.last == "Ce sondage est fermé."


async def test_a_public_poll_is_a_discord_poll_and_nothing_is_stored(setup):
    """Discord counts, shows and closes it; Michel keeps nothing."""
    ext, ctx, repo, _, _ = setup
    await create(ext, ctx, "public", options="", duration="3j", multi=True)
    (sent,) = [r for r in ctx.replies if r.get("poll")]
    poll = sent["poll"]
    assert str(poll.question.text) == "Ce soir ?"
    assert [str(a.poll_media.text) for a in poll.answers] == ["Oui", "Non"]
    sent_as = poll.to_dict()
    assert sent_as["duration"] == 72 and sent_as["allow_multiselect"] is True
    assert repo.items == {}


async def test_a_public_option_longer_than_discord_takes_is_refused(setup):
    ext, ctx, _, _, _ = setup
    await create(ext, ctx, "public", options="Court\n" + "x" * 56)
    assert "55 caractères au plus" in ctx.last
    assert not [r for r in ctx.replies if r.get("poll")]


async def test_a_duration_it_cannot_read_says_how_to_write_it(setup):
    ext, ctx, repo, _, _ = setup
    await create(ext, ctx, duration="bientôt")
    assert ctx.last.startswith("⏱️ Je n'ai pas compris la durée « bientôt »")
    assert repo.items == {}


async def test_the_author_edits_the_question_and_the_options_until_a_vote(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx)
    (poll,) = repo.items.values()
    tools = message.edits[-1]["components"][-1].components
    assert [b.custom_id for b in tools] == [f"poll_edit:{poll.id}"]

    click = FakeCtx(ctx.author, channel, message=message, custom_id=f"poll_edit:{poll.id}")
    await run(PollsExtension.on_poll_edit, ext, click)
    (form,) = click.modals
    assert [c.custom_id for c in form.components] == ["question", "options"]
    assert form.components[1].value == "Pizza\nSushi\nTacos"
    submit = FakeCtx(ctx.author, channel, bot=ctx.bot, custom_id=form.custom_id)
    await run(
        PollsExtension.on_edit_form, ext, submit, question="Demain ?", options="Pizza\nCrêpes"
    )
    assert (poll.question, poll.options) == ("Demain ?", ["Pizza", "Crêpes"])
    assert message.embeds[0].title == "📊 Demain ?"
    labels = [b.label for b in message.edits[-1]["components"][0].components]
    assert labels == ["1. Pizza", "2. Crêpes"]

    await vote(ext, guild, channel, message, poll, 7, 0)
    again = FakeCtx(ctx.author, channel, message=message, custom_id=f"poll_edit:{poll.id}")
    await run(PollsExtension.on_poll_edit, ext, again)
    (late_form,) = again.modals
    assert [c.custom_id for c in late_form.components] == ["question"]


async def test_only_the_author_edits_a_poll(setup):
    ext, ctx, repo, guild, channel = setup
    message = await create(ext, ctx)
    (poll,) = repo.items.values()
    other = voter(guild, channel, message, 8, f"poll_edit:{poll.id}")
    await run(PollsExtension.on_poll_edit, ext, other)
    assert other.modals == [] and other.last.startswith("🔒 Seule la personne")


def test_one_sondage_command_remains():
    """/sondage-anonyme, /sondage-classement and /modifier-sondage are gone."""
    names = {str(getattr(v, "name", "")) for v in vars(PollsExtension).values()}
    assert "sondage" in names
    assert not names & {"sondage-anonyme", "sondage-classement", "modifier-sondage"}
