"""Typed views over the AI config (global ``AI`` section and per-guild ``moduleIA``).

Both are read from plain dicts at call time, so a dashboard edit applies on the
next question without reloading anything.
"""

from __future__ import annotations

import random
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from features.ai.models import (
    DEFAULT_MODELS,
    MAX_COMPARED_MODELS,
    ModelConfig,
    parse_models,
)
from features.ai.prompt import DEFAULT_PERSONA

DEFAULT_MODELS_TO_COMPARE = 3
DEFAULT_VOTE_MINUTES = 10
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_MAX_TOKENS = 500
DEFAULT_MENTION_COOLDOWN = 20


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


@dataclass
class AiGlobalSettings:
    models: dict[str, ModelConfig]
    models_to_compare: int = DEFAULT_MODELS_TO_COMPARE
    default_model: str = ""
    problems: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> AiGlobalSettings:
        """Parse ``config["AI"]``; falls back to the built-in model list when none is valid."""
        section = config.get("AI")
        section = section if isinstance(section, dict) else {}
        models, problems = parse_models(section.get("models"))
        if not models:
            models, _ = parse_models(DEFAULT_MODELS)
            problems.append("aucun modèle valide configuré, liste par défaut utilisée")
        return cls(
            models=models,
            models_to_compare=_int(
                section.get("modelsToCompare"), DEFAULT_MODELS_TO_COMPARE, 2, MAX_COMPARED_MODELS
            ),
            default_model=str(section.get("defaultModel") or "").strip(),
            problems=problems,
        )


@dataclass
class GuildAiSettings:
    persona: str = DEFAULT_PERSONA
    compare_by_default: bool = True
    vote_minutes: int = DEFAULT_VOTE_MINUTES
    history_limit: int = DEFAULT_HISTORY_LIMIT
    max_tokens: int = DEFAULT_MAX_TOKENS
    default_model: str = ""
    mention_replies: bool = False
    mention_cooldown: int = DEFAULT_MENTION_COOLDOWN

    @classmethod
    def from_config(cls, raw: Mapping[str, Any] | None) -> GuildAiSettings:
        """Parse a guild's ``moduleIA`` dict; missing or malformed values use the defaults."""
        raw = raw or {}
        return cls(
            persona=str(raw.get("persona") or "").strip() or DEFAULT_PERSONA,
            compare_by_default=bool(raw.get("compareByDefault", True)),
            vote_minutes=_int(raw.get("voteDurationMinutes"), DEFAULT_VOTE_MINUTES, 1, 24 * 60),
            history_limit=_int(raw.get("historyLimit"), DEFAULT_HISTORY_LIMIT, 0, 50),
            max_tokens=_int(raw.get("maxResponseTokens"), DEFAULT_MAX_TOKENS, 50, 4000),
            default_model=str(raw.get("defaultModel") or "").strip(),
            mention_replies=bool(raw.get("mentionReplies", False)),
            mention_cooldown=_int(
                raw.get("mentionCooldownSeconds"), DEFAULT_MENTION_COOLDOWN, 0, 3600
            ),
        )


def usable_models(
    models: Mapping[str, ModelConfig], configured_apis: Collection[str]
) -> list[ModelConfig]:
    """Models whose API currently has credentials, in config order."""
    return [m for m in models.values() if m.api in configured_apis]


def pick_compare_models(
    candidates: list[ModelConfig], count: int, rng: random.Random | None = None
) -> list[ModelConfig]:
    """A random sample of *count* models (all of them if fewer)."""
    rng = rng or random.Random()
    return rng.sample(candidates, min(count, len(candidates)))


def resolve_default_model(
    candidates: list[ModelConfig], *preferred_keys: str
) -> ModelConfig | None:
    """The first of *preferred_keys* that is usable, else the first usable model."""
    by_key = {m.key: m for m in candidates}
    for key in preferred_keys:
        if key and key in by_key:
            return by_key[key]
    return candidates[0] if candidates else None
