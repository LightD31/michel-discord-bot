"""OpenAI-compatible LLM APIs (``src.integrations.llm``) and the fan-out client."""

import asyncio

import pytest

from features.ai import AiClient, ModelConfig
from src.core.config import config_store
from src.integrations import llm
from src.integrations.llm import (
    Completion,
    ModelPricing,
    parse_nanogpt_catalog,
    parse_openrouter_catalog,
    resolve_pricing,
)


@pytest.fixture
def ai_config():
    """Swap the cached config for a dict the test controls."""
    saved = config_store._data
    data = {"config": {}, "servers": {}}
    config_store._data = data
    llm._catalog_cache.clear()
    yield data["config"]
    config_store._data = saved
    llm._catalog_cache.clear()


# ── Catalog parsing ─────────────────────────────────────────────────────


def test_openrouter_prices_are_per_token_strings():
    models = parse_openrouter_catalog(
        {
            "data": [
                {
                    "id": "openai/gpt-4.1",
                    "name": "GPT-4.1",
                    "pricing": {"prompt": "0.000002", "completion": "0.000008"},
                    "context_length": 1047576,
                },
                {"id": "x/free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
                {"name": "no id"},
            ]
        }
    )
    assert [m.id for m in models] == ["openai/gpt-4.1", "x/free", "openrouter/auto"]
    gpt = models[0]
    assert gpt.prompt_per_m == pytest.approx(2.0)
    assert gpt.completion_per_m == pytest.approx(8.0)
    assert gpt.pricing.calculate_cost(1_000_000, 0) == pytest.approx(2.0)
    assert models[1].free and models[1].name == "x/free"
    assert models[2].pricing is None  # dynamic router price


def test_nanogpt_prices_are_per_million_with_unit():
    models = parse_nanogpt_catalog(
        {
            "data": [
                {
                    "id": "fireworks/ember-1",
                    "name": "Ember 1",
                    "context_length": 1048576,
                    "pricing": {
                        "prompt": 3,
                        "completion": 15,
                        "currency": "USD",
                        "unit": "per_million_tokens",
                    },
                },
                {"id": "eur", "pricing": {"prompt": 1, "completion": 1, "currency": "EUR"}},
                {"id": "odd", "pricing": {"prompt": 1, "completion": 1, "unit": "per_request"}},
            ]
        }
    )
    ember = models[0]
    assert (ember.prompt_per_m, ember.completion_per_m) == (3, 15)
    assert ember.pricing.output_cost_per_token == pytest.approx(15e-6)
    assert models[1].pricing is None and models[2].pricing is None


def test_resolve_pricing_tolerates_routes_and_dates():
    prices = {"a/model": ModelPricing(1, 2), "b/other": ModelPricing(3, 4)}
    assert resolve_pricing("a/model", prices) == prices["a/model"]
    assert resolve_pricing("a/model:free", prices) == prices["a/model"]
    assert resolve_pricing("a/model-20260406", prices) == prices["a/model"]
    assert resolve_pricing("b/other-v2", prices) == prices["b/other"]
    assert resolve_pricing("c/unknown", prices) is None


# ── Settings ────────────────────────────────────────────────────────────


def test_settings_need_key_and_url(ai_config):
    assert not llm.get_api_settings("openrouter").configured
    assert llm.configured_apis() == []

    ai_config["OpenRouter"] = {
        "openrouterApiKey": "k",
        "baseUrl": "https://router.example/api/v1/",
        "appUrl": "https://bot.example",
    }
    ai_config["NanoGPT"] = {"apiKey": "n"}  # no baseUrl: stays off
    ai_config["AI"] = {"requestTimeoutSeconds": 1, "appTitle": "Michel"}
    settings = llm.get_api_settings("openrouter")
    assert settings.configured
    assert settings.base_url == "https://router.example/api/v1"
    assert dict(settings.headers) == {"HTTP-Referer": "https://bot.example", "X-Title": "Michel"}
    assert settings.timeout == 5.0  # clamped
    assert llm.configured_apis() == ["openrouter"]


async def test_get_api_rebuilds_on_change_and_closes(ai_config):
    ai_config["NanoGPT"] = {"apiKey": "n", "baseUrl": "https://nano.example/api/v1"}
    first = await llm.get_api("nanogpt")
    assert first is not None and await llm.get_api("nanogpt") is first
    ai_config["NanoGPT"]["apiKey"] = "rotated"  # pragma: allowlist secret
    second = await llm.get_api("nanogpt")
    assert second is not first and first.client.is_closed()
    ai_config["NanoGPT"]["baseUrl"] = ""
    assert await llm.get_api("nanogpt") is None
    assert second.client.is_closed()
    await llm.close_all()


# ── Catalog fetching ────────────────────────────────────────────────────


async def test_fetch_catalog_requires_configuration(ai_config):
    with pytest.raises(llm.LlmError):
        await llm.fetch_catalog("nanogpt")
    with pytest.raises(llm.LlmError):
        await llm.fetch_catalog("ollama")


async def test_fetch_catalog_is_memoized_and_uses_detailed_listing(ai_config, monkeypatch):
    ai_config["NanoGPT"] = {"apiKey": "n", "baseUrl": "https://nano.example/api/v1"}
    calls = []

    async def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        return {"data": [{"id": "m", "pricing": {"prompt": 1, "completion": 2}}]}

    monkeypatch.setattr(llm, "fetch", fake_fetch)
    models = await llm.fetch_catalog("nanogpt")
    assert [m.id for m in models] == ["m"]
    await llm.fetch_catalog("nanogpt")
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://nano.example/api/v1/models"
    assert kwargs["params"] == {"detailed": "true"}
    assert kwargs["headers"]["Authorization"] == "Bearer n"


async def test_fetch_catalog_wraps_upstream_errors(ai_config, monkeypatch):
    ai_config["OpenRouter"] = {"openrouterApiKey": "k", "baseUrl": "https://r.example"}

    async def failing_fetch(url, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm, "fetch", failing_fetch)
    with pytest.raises(llm.LlmError):
        await llm.fetch_catalog("openrouter")


# ── Fan-out client ──────────────────────────────────────────────────────


class _FakeApi:
    def __init__(self, name, behaviour):
        self.name = name
        self.behaviour = behaviour
        self.started: list[str] = []

    async def complete(self, model_id, messages, max_tokens):
        self.started.append(model_id)
        await asyncio.sleep(0.05)
        outcome = self.behaviour[model_id]
        if isinstance(outcome, Exception):
            raise outcome
        return Completion(content=outcome, model=model_id, input_tokens=10, output_tokens=5)

    async def cost_for(self, completion):
        return 0.001


def _model(key, api="openrouter"):
    return ModelConfig(key=key, api=api, model_id=f"{key}/m", display_name=key.upper())


async def test_complete_many_runs_in_parallel_and_isolates_failures():
    apis = {
        "openrouter": _FakeApi(
            "openrouter",
            {"a/m": "<response>A</response>", "b/m": TimeoutError(), "c/m": RuntimeError("500")},
        ),
        "nanogpt": _FakeApi("nanogpt", {"d/m": "<response></response>"}),
    }

    async def get_api(name):
        return apis.get(name)

    client = AiClient(get_api)
    models = [_model("a"), _model("b"), _model("c"), _model("d", "nanogpt"), _model("e", "other")]
    loop = asyncio.get_running_loop()
    start = loop.time()
    answers = await client.complete_many(models, [{"role": "user", "content": "q"}], 100)
    assert loop.time() - start < 0.15  # 4 calls of 50 ms ran concurrently

    assert [a.model.key for a in answers] == ["a", "b", "c", "d", "e"]
    assert answers[0].ok and answers[0].content == "A" and answers[0].cost == 0.001
    assert answers[1].error == "délai dépassé"
    assert answers[2].error == "500"
    assert answers[3].error == "réponse vide"
    assert "non configurée" in answers[4].error
    assert [a.ok for a in answers] == [True, False, False, False, False]
