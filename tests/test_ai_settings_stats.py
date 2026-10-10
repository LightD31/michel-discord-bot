"""AI config parsing, model selection and the model leaderboard (pure)."""

import random
from datetime import datetime

from features.ai import (
    AiGlobalSettings,
    Comparison,
    ComparisonAnswer,
    GuildAiSettings,
    compute_model_stats,
    parse_models,
    pick_compare_models,
    resolve_default_model,
    usable_models,
)
from features.ai.prompt import DEFAULT_PERSONA


def test_parse_models_defaults_and_problems():
    models, problems = parse_models(
        [
            {"provider": "gpt", "model_id": "openai/gpt-4.1", "display_name": "GPT"},
            {"provider": "Nano GPT:X", "api": "nanogpt", "model_id": "x/y"},
            {"model_id": "deepseek/v3"},  # key derived from the id
            {"provider": "gpt", "model_id": "dup"},
            {"provider": "bad", "api": "ollama", "model_id": "m"},
            {"provider": "nomodel"},
            "junk",
        ]
    )
    assert list(models) == ["gpt", "nano-gpt-x", "deepseek-v3"]
    assert models["gpt"].api == "openrouter"  # legacy entries had no api field
    assert models["nano-gpt-x"].api == "nanogpt"
    assert models["nano-gpt-x"].display_name == "x/y"
    assert len(problems) == 4


def test_global_settings_fall_back_to_default_models():
    settings = AiGlobalSettings.from_config({"AI": {"models": [], "modelsToCompare": 99}})
    assert settings.models  # built-in list
    assert settings.problems
    assert settings.models_to_compare == 5  # clamped to one button row


def test_guild_settings_defaults_and_clamping():
    assert GuildAiSettings.from_config(None).persona == DEFAULT_PERSONA
    settings = GuildAiSettings.from_config(
        {
            "persona": " Pirate ",
            "historyLimit": 500,
            "maxResponseTokens": "abc",
            "mentionReplies": 1,
        }
    )
    assert settings.persona == "Pirate"
    assert settings.history_limit == 50
    assert settings.max_tokens == 500
    assert settings.mention_replies is True


def _models():
    models, _ = parse_models(
        [
            {"provider": "a", "model_id": "a/a"},
            {"provider": "b", "api": "nanogpt", "model_id": "b/b"},
            {"provider": "c", "model_id": "c/c"},
        ]
    )
    return models


def test_usable_models_filters_unconfigured_apis():
    assert [m.key for m in usable_models(_models(), ["openrouter"])] == ["a", "c"]
    assert usable_models(_models(), []) == []


def test_pick_and_resolve():
    candidates = usable_models(_models(), ["openrouter", "nanogpt"])
    picked = pick_compare_models(candidates, 2, random.Random(1))
    assert len(picked) == 2 and len({m.key for m in picked}) == 2
    assert len(pick_compare_models(candidates, 10)) == 3
    assert resolve_default_model(candidates, "", "c").key == "c"
    assert resolve_default_model(candidates, "b", "c").key == "b"
    assert resolve_default_model(candidates, "gone").key == "a"
    assert resolve_default_model([], "a") is None


def _comparison(votes, keys=("a", "b", "c")):
    return Comparison(
        question="q",
        asker_id="1",
        channel_id="2",
        answers=[ComparisonAnswer(k, "openrouter", f"{k}/{k}", k.upper(), "x") for k in keys],
        closes_at=datetime(2026, 1, 1),
        votes=votes,
    )


def test_stats_count_votes_appearances_and_ties():
    stats = compute_model_stats(
        [
            _comparison({"u1": "a", "u2": "a", "u3": "b"}),
            _comparison({"u1": "b", "u2": "c"}),  # tie between b and c
            _comparison({}),  # ignored: nobody voted
            _comparison({"u1": "a"}, keys=("a", "b")),
        ]
    )
    by_key = {s.key: s for s in stats}
    assert (by_key["a"].votes, by_key["a"].appearances, by_key["a"].wins) == (3, 3, 2)
    assert (by_key["b"].votes, by_key["b"].appearances, by_key["b"].wins) == (2, 3, 1)
    assert (by_key["c"].votes, by_key["c"].appearances, by_key["c"].wins) == (1, 2, 1)
    assert [s.key for s in stats] == ["a", "c", "b"]  # by win rate, then votes
    assert by_key["c"].win_rate == 0.5
