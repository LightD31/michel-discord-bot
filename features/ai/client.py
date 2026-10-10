"""Ask one or several configured models — parallel, bounded, never raising per model."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence

from features.ai.models import ModelAnswer, ModelConfig
from features.ai.prompt import extract_response
from src.core import logging as logutil
from src.integrations import llm

logger = logutil.init_logger(__name__)

ApiGetter = Callable[[str], Awaitable[llm.LlmApi | None]]


class AiClient:
    """Routes each model to its API and turns every failure into an error answer."""

    def __init__(self, get_api: ApiGetter = llm.get_api) -> None:
        self._get_api = get_api

    async def complete(
        self, model: ModelConfig, messages: list[dict[str, str]], max_tokens: int
    ) -> ModelAnswer:
        api = await self._get_api(model.api)
        if api is None:
            return ModelAnswer(model, error=f"API {model.api} non configurée")
        start = time.monotonic()
        try:
            completion = await api.complete(model.model_id, messages, max_tokens)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return ModelAnswer(model, error="délai dépassé", latency=time.monotonic() - start)
        except Exception as e:
            logger.error("Error calling %s (%s): %s", model.key, model.model_id, e)
            return ModelAnswer(
                model, error=str(e) or type(e).__name__, latency=time.monotonic() - start
            )
        latency = time.monotonic() - start
        content = extract_response(completion.content)
        try:
            cost = await api.cost_for(completion)
        except Exception as e:  # pricing is best-effort, never lose the answer for it
            logger.debug("Cost lookup failed for %s: %s", completion.model, e)
            cost = None
        answer = ModelAnswer(
            model,
            content=content,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost=cost,
            latency=latency,
            error=None if content else "réponse vide",
        )
        _log_answer(answer, completion.model)
        return answer

    async def complete_many(
        self, models: Sequence[ModelConfig], messages: list[dict[str, str]], max_tokens: int
    ) -> list[ModelAnswer]:
        """All *models* in parallel; the result keeps *models*' order."""
        answers = await asyncio.gather(*(self.complete(m, messages, max_tokens) for m in models))
        total = sum(a.cost or 0.0 for a in answers)
        logger.info("Total command cost: $%.5f", total)
        return list(answers)


def _log_answer(answer: ModelAnswer, served_model: str) -> None:
    cost = f"${answer.cost:.5f}" if answer.cost is not None else "unknown"
    logger.info(
        "Model: %s via %s | Cost: %s | %d tks in | %d tks out | %.1fs",
        served_model,
        answer.model.api,
        cost,
        answer.input_tokens,
        answer.output_tokens,
        answer.latency,
    )
