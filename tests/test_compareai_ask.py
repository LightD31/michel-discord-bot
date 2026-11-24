"""/ask (extensions/compareai/ask.py): the quick answer, the comparison, and what stops them.

The models are faked (``AiClient``), the daily counters too
(``AiUsageRepository``, checked against MongoDB in ``test_mongo_ai_usage.py``),
and the comparisons kept in memory.
"""

from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, run

from extensions.compareai import ask as ask_mod
from extensions.compareai.ask import VOTE_PREFIX, AskMixin
from features.ai import ModelAnswer, RecentAnswers
from features.ai.quota import QUOTA_UNAVAILABLE
from src.core.errors import DatabaseError

MODELS = [
    {"api": "openrouter", "model_id": "vendor/alpha", "key": "alpha", "display_name": "Alpha"},
    {"api": "openrouter", "model_id": "vendor/beta", "key": "beta", "display_name": "Beta"},
]


TEXTS = {"alpha": "Un marathon caritatif.", "beta": "Un événement en direct."}


class Client:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.asked = []

    def _answer(self, model):
        self.asked.append(model.key)
        if model.key in self.failing:
            return ModelAnswer(model=model, error="HTTP 500")
        return ModelAnswer(model=model, content=TEXTS[model.key])

    async def complete(self, model, messages, max_tokens, *, guild_id):
        return self._answer(model)

    async def complete_many(self, models, messages, max_tokens, *, guild_id):
        return [self._answer(m) for m in models]


class Repo:
    def __init__(self, fail_create=False):
        self.created = []
        self.message_ids = {}
        self.fail_create = fail_create

    async def known_message_ids(self, ids):
        return set()

    async def create(self, comparison):
        if self.fail_create:
            raise DatabaseError("down")
        comparison.id = "abababababababababababab"
        self.created.append(comparison)
        return comparison.id

    async def set_message_ids(self, cid, ids):
        self.message_ids[cid] = ids


class Usage:
    calls: list = []
    hit: str | None = None
    down = False

    async def reserve(self, user_id, calls, limits, now):
        if Usage.down:
            raise DatabaseError("down")
        Usage.calls.append((user_id, calls))
        return Usage.hit


@pytest.fixture
def setup(monkeypatch, store_config):
    def configure(models=MODELS, ai=None, **module):
        store_config(
            {
                "config": {"AI": {"models": models, **(ai or {})}},
                "servers": {"1": {"moduleIA": {"enabled": True, "historyLimit": 0, **module}}},
            }
        )

    configure()
    Usage.calls, Usage.hit, Usage.down = [], None, False
    repo = Repo()
    monkeypatch.setattr(ask_mod, "repo_for", lambda gid: repo)
    monkeypatch.setattr(ask_mod, "AiUsageRepository", Usage)
    monkeypatch.setattr(ask_mod.llm, "configured_apis", lambda guild_id: ["openrouter"])
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    ext = object.__new__(AskMixin)
    ext.bot = SimpleNamespace(
        user=SimpleNamespace(id=99),
        cache=SimpleNamespace(get_member=lambda g, u: None, get_user=lambda u: None),
    )
    ext.ai_client = Client()
    ext.recent_answers = RecentAnswers()
    ctx = FakeCtx(FakeMember(7, "Léa", guild=guild), channel)
    return SimpleNamespace(ext=ext, ctx=ctx, repo=repo, configure=configure)


async def ask(s, question="C'est quoi le Zevent ?", mode=None):
    await run(AskMixin.ask_question, s.ext, s.ctx, question=question, mode=mode)
    return " ".join(str(r) for r in s.ctx.replies)


async def test_a_quick_answer_names_its_model_and_pings_nobody(setup):
    said = await ask(setup, mode=ask_mod.MODE_QUICK)

    assert TEXTS["alpha"] in said and "modèle : Alpha" in said
    assert Usage.calls == [("7", 1)]
    assert all(r["allowed_mentions"] is ask_mod.NO_PINGS for r in setup.ctx.replies)
    assert setup.repo.created == []


async def test_a_failing_default_model_is_retried_on_another(setup):
    setup.ext.ai_client = Client(failing={"alpha"})
    said = await ask(setup, mode=ask_mod.MODE_QUICK)
    assert TEXTS["beta"] in said
    assert Usage.calls == [("7", 1), ("7", 1)]  # the retry is counted too


async def test_no_model_answering_is_said(setup):
    setup.ext.ai_client = Client(failing={"alpha", "beta"})
    said = await ask(setup, mode=ask_mod.MODE_QUICK)
    assert "Aucun modèle n'a pu répondre" in said


async def test_a_comparison_is_stored_with_its_vote_buttons(setup):
    said = await ask(setup, mode=ask_mod.MODE_COMPARE)

    (comparison,) = setup.repo.created
    assert {a.display_name for a in comparison.answers} == {"Alpha", "Beta"}
    assert "Alpha" not in said  # the models stay anonymous until the vote closes
    buttons = setup.ctx.replies[-1]["components"]
    assert [b.custom_id for b in buttons] == [
        f"{VOTE_PREFIX}:g:{comparison.id}:0",
        f"{VOTE_PREFIX}:g:{comparison.id}:1",
    ]
    assert setup.repo.message_ids[comparison.id]
    assert Usage.calls == [("7", 2)]  # one call per compared model, reserved up front


async def test_a_comparison_with_one_answer_is_a_quick_answer(setup):
    setup.ext.ai_client = Client(failing={"beta"})
    said = await ask(setup, mode=ask_mod.MODE_COMPARE)
    assert "les autres modèles n'ont pas répondu" in said
    assert setup.repo.created == []


async def test_a_comparison_the_database_refuses_is_posted_without_votes(monkeypatch, setup):
    repo = Repo(fail_create=True)
    monkeypatch.setattr(ask_mod, "repo_for", lambda gid: repo)
    said = await ask(setup, mode=ask_mod.MODE_COMPARE)
    assert "Alpha" in said and "Beta" in said  # closed at once: the models are named
    assert all(not r.get("components") for r in setup.ctx.replies)


async def test_nothing_is_asked_without_a_configured_api(monkeypatch, setup):
    monkeypatch.setattr(ask_mod.llm, "configured_apis", lambda guild_id: [])
    said = await ask(setup)
    assert "IA non configurée" in said
    assert Usage.calls == [] and setup.ext.ai_client.asked == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"hit": "user"}, "limite"),
        ({"down": True}, QUOTA_UNAVAILABLE),
    ],
)
async def test_a_spent_or_unreadable_quota_refuses(setup, change, expected):
    for name, value in change.items():
        setattr(Usage, name, value)
    said = await ask(setup)
    assert expected.lower() in said.lower()
    assert setup.ext.ai_client.asked == []


async def test_a_paused_ai_refuses_before_counting(setup):
    setup.configure(ai={"paused": True})
    await ask(setup)
    assert Usage.calls == [] and setup.ext.ai_client.asked == []
    assert setup.ctx.replies and setup.ctx.replies[0].get("ephemeral")


async def test_the_mentions_in_the_question_are_resolved_from_the_cache(setup):
    setup.ext.bot.cache = SimpleNamespace(
        get_member=lambda g, u: SimpleNamespace(display_name="Hugo") if u == "8" else None,
        get_user=lambda u: None,
    )
    assert setup.ext._resolve_mentions(1, "Que pense <@8> de <@9> ?") == "Que pense @Hugo de <@9> ?"
