"""The /ask comparisons' votes, their closing and /ask-stats (extensions/compareai/voting.py).

The repository is replaced by an in-memory one keeping the real one's
contract: ``set_vote`` answers ``None`` once the comparison is closed, and
``close`` answers the comparison it closed. Its MongoDB side is checked in
``tests/test_mongo_ai_comparisons.py``.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from discord_fakes import FakeBot, FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeMessage, run

from extensions.compareai import voting
from extensions.compareai.voting import VotingMixin
from features.ai import Comparison
from features.ai.models import ComparisonAnswer
from features.ai.quota import MODULE_DISABLED
from src.core.errors import DatabaseError

CID = "abababababababababababab"


def comparison(**changes) -> Comparison:
    base = Comparison(
        question="Qui est le meilleur ?",
        asker_id="5",
        channel_id="10",
        answers=[
            ComparisonAnswer("a", "openrouter", "m-a", "Modèle A", "Réponse A"),
            ComparisonAnswer("b", "openrouter", "m-b", "Modèle B", "Réponse B"),
        ],
        closes_at=datetime.now(UTC) + timedelta(hours=1),
        guild_id="1",
        message_ids=["300"],
        id=CID,
    )
    return replace(base, **changes)


class Repo:
    def __init__(self, *items: Comparison, fail: bool = False):
        self.items = {c.id: c for c in items}
        self.fail = fail
        self.guild_id = "1"

    async def get(self, cid):
        if self.fail:
            raise DatabaseError("down")
        return self.items.get(cid)

    async def set_vote(self, cid, user_id, key):
        found = self.items.get(cid)
        if found is None or found.closed:
            return None
        found.votes[user_id] = key
        return replace(found, votes=dict(found.votes))

    async def due_for_close(self, now):
        return [c for c in self.items.values() if not c.closed and c.closes_at <= now]

    async def close(self, cid):
        found = self.items[cid]
        found.closed = True
        return found

    async def list_for_stats(self):
        if self.fail:
            raise DatabaseError("down")
        return [c for c in self.items.values() if c.votes]


@pytest.fixture
def setup(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleIA": {"enabled": True}}}})
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    bot = FakeBot(guild)
    ext = object.__new__(VotingMixin)
    ext.bot = bot

    def use(repo):
        monkeypatch.setattr(voting, "repo_for", lambda gid: repo)
        return repo

    return ext, guild, channel, use


def click(guild, channel, index, *, author=None, message=None):
    message = message or FakeMessage(300, content="", channel=channel)
    channel.messages[message.id] = message
    return FakeCtx(
        author or FakeMember(7, guild=guild),
        channel,
        message=message,
        custom_id=f"aivote:g:{CID}:{index}",
    )


async def test_a_vote_is_recorded_changed_and_repeated(setup):
    ext, guild, channel, use = setup
    repo = use(Repo(comparison()))
    member = FakeMember(7, guild=guild)

    ctx = click(guild, channel, 0, author=member)
    await run(VotingMixin.on_ai_vote, ext, ctx)
    assert ctx.replies[-1]["content"] == "Vote enregistré pour la réponse 1."
    assert repo.items[CID].votes == {"7": "a"}
    assert ctx.message.edits  # the tally shows the vote

    ctx = click(guild, channel, 0, author=member)
    await run(VotingMixin.on_ai_vote, ext, ctx)
    assert ctx.replies[-1]["content"] == "Tu as déjà voté pour la réponse 1."

    ctx = click(guild, channel, 1, author=member)
    await run(VotingMixin.on_ai_vote, ext, ctx)
    assert ctx.replies[-1]["content"] == "Vote changé : réponse 2."
    assert repo.items[CID].votes == {"7": "b"}
    assert all(r.get("ephemeral") for r in ctx.replies)


async def test_a_click_on_a_closed_comparison_shows_the_results(setup):
    ext, guild, channel, use = setup
    use(Repo(comparison(closed=True, votes={"8": "a"})))
    ctx = click(guild, channel, 0)

    await run(VotingMixin.on_ai_vote, ext, ctx)

    assert ctx.replies == [{"content": "Le vote est terminé.", "ephemeral": True}]
    assert ctx.message.edits[-1]["components"] == []
    assert "Modèle A" in ctx.message.content  # the models are revealed


async def test_a_comparison_closed_between_read_and_vote_shows_the_results(setup):
    ext, guild, channel, use = setup
    repo = use(Repo(comparison()))

    async def closed_meanwhile(cid, user_id, key):
        return None

    repo.set_vote = closed_meanwhile
    ctx = click(guild, channel, 0)
    await run(VotingMixin.on_ai_vote, ext, ctx)
    assert ctx.replies[-1]["content"] == "Le vote est terminé."


async def test_a_gone_comparison_and_an_outage_are_said(setup):
    ext, guild, channel, use = setup
    use(Repo())
    ctx = click(guild, channel, 0)
    await run(VotingMixin.on_ai_vote, ext, ctx)
    assert ctx.replies[-1]["content"] == "Comparaison introuvable."

    use(Repo(comparison(), fail=True))
    ctx = click(guild, channel, 0)
    await run(VotingMixin.on_ai_vote, ext, ctx)
    assert ctx.replies[-1]["content"].startswith("❌")
    assert not ctx.message.edits


async def test_the_task_reveals_the_models_of_due_comparisons(setup):
    ext, guild, channel, use = setup
    due = comparison(closes_at=datetime.now(UTC) - timedelta(minutes=1), votes={"8": "b"})
    open_one = comparison(id="ffffffffffffffffffffffff", message_ids=["301"])
    repo = use(Repo(due, open_one))
    message = FakeMessage(300, content="Réponses anonymes", channel=channel)
    channel.messages[300] = message

    await VotingMixin.close_due_comparisons.callback(ext)

    assert repo.items[CID].closed and not repo.items[open_one.id].closed
    assert message.edits[-1]["components"] == []
    assert "Modèle B" in message.content


async def test_a_closed_comparison_whose_message_is_gone_stays_closed(setup):
    ext, _guild, _channel, use = setup
    repo = use(Repo(comparison(closes_at=datetime.now(UTC) - timedelta(minutes=1))))

    await VotingMixin.close_due_comparisons.callback(ext)  # message 300 was deleted

    assert repo.items[CID].closed


async def test_the_leaderboard_ranks_the_voted_models(setup):
    ext, guild, channel, use = setup
    use(Repo(comparison(votes={"7": "a", "8": "a", "9": "b"})))
    ctx = FakeCtx(FakeMember(7, guild=guild), channel)

    await run(VotingMixin.ask_stats, ext, ctx)

    embed = ctx.replies[-1]["embed"]
    assert embed.description.splitlines()[0].startswith("**1. Modèle A** — 1/1 victoires")
    assert "1 comparaison votée" in embed.footer.text


async def test_the_leaderboard_without_votes_invites_to_ask(setup):
    ext, guild, channel, use = setup
    use(Repo(comparison()))
    ctx = FakeCtx(FakeMember(7, guild=guild), channel)
    await run(VotingMixin.ask_stats, ext, ctx)
    assert "/ask" in ctx.replies[-1]["embed"].description


async def test_the_leaderboard_is_refused_where_the_module_is_off(setup, store_config):
    ext, guild, channel, use = setup
    store_config({"servers": {"1": {"moduleIA": {"enabled": False}}}})
    ctx = FakeCtx(FakeMember(7, guild=guild), channel)
    await run(VotingMixin.ask_stats, ext, ctx)
    assert ctx.replies == [{"content": MODULE_DISABLED, "ephemeral": True}]

    store_config({"servers": {"1": {"moduleIA": {"enabled": True}}}})
    use(Repo(fail=True))
    await run(VotingMixin.ask_stats, ext, ctx)
    assert ctx.replies[-1]["content"].startswith("❌")
