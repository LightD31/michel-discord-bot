"""AI cost guards: the policy, the daily counters, and where /ask and mentions apply them."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import extensions.compareai.ask as ask_module
import features.ai.usage as usage_module
from extensions.compareai.ask import MODE_COMPARE, MODE_QUICK, AskMixin
from features.ai import AiGlobalSettings, AiLimits, AiUsageRepository, access_refusal
from features.ai.quota import (
    EXTERNAL_DISABLED,
    EXTERNAL_NOT_ALLOWED,
    GLOBAL,
    GLOBAL_LIMIT_REACHED,
    PAUSED,
    QUOTA_UNAVAILABLE,
    USER,
    USER_LIMIT_REACHED,
    day_expiry,
    day_key,
)
from src.core.errors import DatabaseError

NOW = datetime(2026, 11, 6, 23, 30, tzinfo=UTC)

# ── Policy ──────────────────────────────────────────────────────────────


def test_limits_parse_with_safe_defaults():
    limits = AiLimits.from_section({})
    assert limits == AiLimits()
    assert limits.daily_calls > 0 and limits.user_daily_calls > 0  # bounded out of the box

    parsed = AiLimits.from_section(
        {
            "paused": True,
            "dailyCallLimit": "200",
            "userDailyCallLimit": -5,  # clamped, i.e. unlimited
            "externalUse": False,
            "externalUserIds": [" 42 ", "", 7],
        }
    )
    assert parsed.paused and not parsed.external_use
    assert parsed.daily_calls == 200 and parsed.user_daily_calls == 0
    assert parsed.external_user_ids == frozenset({"42", "7"})


def test_global_settings_carry_the_limits():
    settings = AiGlobalSettings.from_config({"AI": {"dailyCallLimit": 5}})
    assert settings.limits.daily_calls == 5


def test_access_rules():
    open_limits = AiLimits()
    assert access_refusal(open_limits, user_id="1", in_enabled_guild=False) is None
    assert access_refusal(AiLimits(paused=True), user_id="1", in_enabled_guild=True) == PAUSED

    closed = AiLimits(external_use=False)
    assert access_refusal(closed, user_id="1", in_enabled_guild=False) == EXTERNAL_DISABLED
    assert access_refusal(closed, user_id="1", in_enabled_guild=True) is None

    allowlist = AiLimits(external_user_ids=frozenset({"42"}))
    assert access_refusal(allowlist, user_id="42", in_enabled_guild=False) is None
    assert access_refusal(allowlist, user_id="7", in_enabled_guild=False) == EXTERNAL_NOT_ALLOWED
    assert access_refusal(allowlist, user_id="7", in_enabled_guild=True) is None


def test_days_are_utc():
    assert day_key(NOW) == "2026-11-06"
    assert day_expiry(NOW) == datetime(2026, 11, 8, tzinfo=UTC)


# ── Counters (in-memory stand-in for the MongoDB collection) ──────────────


class FakeUsageCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def find_one_and_update(self, query, update, upsert, return_document):
        doc = self.docs.get(query["_id"])
        if doc is None:
            doc = {"_id": query["_id"], **update.get("$setOnInsert", {})}
            self.docs[query["_id"]] = doc
        for field, delta in update["$inc"].items():
            doc[field] = doc.get(field, 0) + delta
        return dict(doc)

    def count(self, key):
        return self.docs.get(key, {}).get("count", 0)


@pytest.fixture
def usage(monkeypatch):
    col = FakeUsageCollection()
    monkeypatch.setattr(usage_module.AiUsageRepository, "_col", lambda self: col)
    return col


def reserve(user_id, calls, limits):
    return asyncio.run(AiUsageRepository().reserve(user_id, calls, limits, NOW))


def test_reservations_count_until_the_user_limit(usage):
    limits = AiLimits(daily_calls=100, user_daily_calls=5)
    assert reserve("u1", 3, limits) is None
    assert reserve("u1", 2, limits) is None
    assert reserve("u1", 1, limits) == USER
    assert usage.count("2026-11-06:user:u1") == 5  # the refused call was not counted
    assert usage.count("2026-11-06:all") == 5
    assert reserve("u2", 3, limits) is None  # someone else still has their quota


def test_global_limit_refuses_and_rolls_back(usage):
    limits = AiLimits(daily_calls=4, user_daily_calls=10)
    assert reserve("u1", 3, limits) is None
    assert reserve("u2", 3, limits) == GLOBAL
    assert usage.count("2026-11-06:all") == 3
    assert usage.count("2026-11-06:user:u2") == 0


def test_zero_means_unlimited(usage):
    limits = AiLimits(daily_calls=0, user_daily_calls=0)
    for _ in range(50):
        assert reserve("u1", 5, limits) is None
    assert usage.count("2026-11-06:all") == 250


def test_counters_expire(usage):
    reserve("u1", 1, AiLimits())
    assert usage.docs["2026-11-06:all"]["expires_at"] == day_expiry(NOW)


# ── /ask and mention wiring ─────────────────────────────────────────────


def model(key):
    return SimpleNamespace(key=key, display_name=key)


class Harness(AskMixin):
    """AskMixin with the Discord and LLM edges stubbed out."""

    def __init__(self, candidates, quota_answer=None):
        self.candidates = candidates
        self.quota_answer = quota_answer
        self.reserved: list[int] = []
        self.history_read = False
        self.compare_count = None
        self.quick = False

    async def _usable_models(self):
        return self.candidates

    async def _reserve_calls(self, user_id, calls, limits):
        self.reserved.append(calls)
        return self.quota_answer

    async def _gather_history(self, *args, **kwargs):
        self.history_read = True
        return []

    def _build_messages(self, settings, **kwargs):
        return [{"role": "user", "content": "q"}]

    def _resolve_mentions(self, guild_id, content):
        return content

    async def _ask_compare(
        self, ctx, question, candidates, settings, messages, scope, repo, *, count
    ):
        self.compare_count = count

    async def _ask_quick(self, ctx, question, candidates, settings, messages, note=None, *, limits):
        self.quick = True


@pytest.fixture
def wired(monkeypatch):
    errors: list[str] = []

    async def fake_send_error(_ctx, message):
        errors.append(message)

    state = {"limits": AiLimits(), "enabled": True, "compare": 3}
    monkeypatch.setattr(ask_module, "send_error", fake_send_error)
    monkeypatch.setattr(
        ask_module,
        "global_settings",
        lambda: SimpleNamespace(
            limits=state["limits"], models_to_compare=state["compare"], default_model=""
        ),
    )
    monkeypatch.setattr(ask_module, "module_enabled", lambda gid: state["enabled"])
    monkeypatch.setattr(
        ask_module,
        "guild_settings",
        lambda gid: SimpleNamespace(compare_by_default=True, history_limit=0, default_model=""),
    )
    monkeypatch.setattr(ask_module, "repo_for", lambda scope, gid: None)
    monkeypatch.setattr(ask_module, "scope_for", lambda gid: "g")
    return errors, state


def ask(harness, mode=None):
    ctx = SimpleNamespace(
        guild_id=1,
        guild=SimpleNamespace(name="srv"),
        channel=SimpleNamespace(name="général"),
        author=SimpleNamespace(id=7, display_name="Tom"),
    )
    asyncio.run(harness._handle_ask(ctx, "Question ?", mode))


def test_compare_reserves_one_call_per_model(wired):
    harness = Harness([model(k) for k in "abcd"])
    ask(harness, MODE_COMPARE)
    assert harness.reserved == [3] and harness.compare_count == 3


def test_quick_reserves_one_call(wired):
    harness = Harness([model(k) for k in "abcd"])
    ask(harness, MODE_QUICK)
    assert harness.reserved == [1] and harness.quick


def test_compare_with_one_model_is_quick(wired):
    harness = Harness([model("a")])
    ask(harness, MODE_COMPARE)
    assert harness.reserved == [1] and harness.quick


def test_quota_refusal_stops_before_any_work(wired):
    errors, _ = wired
    harness = Harness([model(k) for k in "abc"], quota_answer=USER_LIMIT_REACHED)
    ask(harness)
    assert errors == [USER_LIMIT_REACHED]
    assert not harness.history_read and harness.compare_count is None and not harness.quick


def test_paused_and_external_rules_apply_before_counting(wired):
    errors, state = wired
    state["limits"] = AiLimits(paused=True)
    harness = Harness([model("a")])
    ask(harness)
    assert errors == [PAUSED] and harness.reserved == []

    errors.clear()
    state["limits"] = AiLimits(external_use=False)
    state["enabled"] = False  # a server where the AI module is off (user install)
    ask(harness)
    assert errors == [EXTERNAL_DISABLED] and harness.reserved == []


def test_retry_on_another_model_needs_its_own_quota(monkeypatch):
    calls: list[str] = []

    class Client:
        async def complete(self, m, messages, max_tokens):
            calls.append(m.key)
            return SimpleNamespace(ok=False, error="boom", model=m)

    harness = Harness([model("a"), model("b")], quota_answer=GLOBAL_LIMIT_REACHED)
    harness.ai_client = Client()
    monkeypatch.setattr(
        ask_module, "global_settings", lambda: SimpleNamespace(default_model="", limits=AiLimits())
    )
    settings = SimpleNamespace(default_model="", max_tokens=100)
    answer = asyncio.run(
        harness._answer_once(harness.candidates, settings, [], user_id="7", limits=AiLimits())
    )
    assert answer is None
    assert calls == ["a"]  # the fallback model was never called
    assert harness.reserved == [1]


def test_quota_check_fails_closed(monkeypatch):
    class BrokenRepo:
        async def reserve(self, *args):
            raise DatabaseError("mongo down")

    monkeypatch.setattr(ask_module, "AiUsageRepository", BrokenRepo)
    refusal = asyncio.run(AskMixin._reserve_calls(object(), "7", 1, AiLimits()))
    assert refusal == QUOTA_UNAVAILABLE


def test_limit_messages():
    from features.ai import limit_message

    assert limit_message(USER) == USER_LIMIT_REACHED
    assert limit_message(GLOBAL) == GLOBAL_LIMIT_REACHED


def test_mentions_respect_the_kill_switch_and_quota(monkeypatch):
    import extensions.compareai.mentions as mentions_module
    from extensions.compareai.mentions import QUOTA_EMOJI, MentionsMixin

    class Mentions(MentionsMixin):
        def __init__(self, quota_answer):
            self.quota_answer = quota_answer
            self.reactions: list[str] = []
            self.answered = False

        async def _usable_models(self):
            return [model("a")]

        async def _reserve_calls(self, user_id, calls, limits):
            return self.quota_answer

        async def _react(self, message, emoji):
            self.reactions.append(emoji)

        async def _answer_once(self, *args, **kwargs):
            self.answered = True

    message = SimpleNamespace(author=SimpleNamespace(id=7))
    for limits, quota_answer in [(AiLimits(paused=True), None), (AiLimits(), USER_LIMIT_REACHED)]:
        monkeypatch.setattr(
            mentions_module, "global_settings", lambda limits=limits: SimpleNamespace(limits=limits)
        )
        handler = Mentions(quota_answer)
        asyncio.run(handler._answer_mention(message, "q", "1", SimpleNamespace()))
        assert handler.reactions == [QUOTA_EMOJI] and not handler.answered
