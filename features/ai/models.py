"""Plain data types for the AI answers feature, and the model registry parser."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.integrations.llm import API_OPENROUTER, SUPPORTED_APIS

# Discord shows at most 5 buttons per row; a comparison never needs more.
MAX_COMPARED_MODELS = 5


@dataclass(frozen=True)
class ModelConfig:
    """One configured model.

    ``key`` is the stable identifier used in votes and stats (stored as
    ``provider`` in config for backward compatibility); ``api`` names the
    OpenAI-compatible API the model is reached through.
    """

    key: str
    api: str
    model_id: str
    display_name: str


# Fallback when the config lists no valid model.
DEFAULT_MODELS: list[dict[str, str]] = [
    {"provider": "openai", "model_id": "openai/gpt-4.1", "display_name": "OpenAI GPT-4.1"},
    {
        "provider": "anthropic",
        "model_id": "anthropic/claude-opus-4.5",
        "display_name": "Anthropic Claude Opus 4.5",
    },
    {
        "provider": "deepseek",
        "model_id": "deepseek/deepseek-chat-v3-0324",
        "display_name": "DeepSeek Chat v3-0324",
    },
    {
        "provider": "gemini",
        "model_id": "google/gemini-3-pro-preview",
        "display_name": "Google Gemini 3 Pro Preview",
    },
]

_KEY_SANITIZER = re.compile(r"[^a-z0-9_.-]+")


def slugify_key(value: str) -> str:
    """A vote-safe key: lowercase, no colons (custom ids use ``:`` as separator)."""
    return _KEY_SANITIZER.sub("-", value.strip().lower()).strip("-")[:60]


def parse_models(raw: Any) -> tuple[dict[str, ModelConfig], list[str]]:
    """Parse the ``AI.models`` config list.

    Returns ``(models_by_key, problems)``. Entries without a ``model_id`` or
    with an unknown ``api`` are skipped and reported; a missing ``api``
    defaults to OpenRouter (the only provider before NanoGPT was added), a
    missing key is derived from the model id, and duplicate keys keep the
    first entry.
    """
    models: dict[str, ModelConfig] = {}
    problems: list[str] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            problems.append(f"entrée invalide : {entry!r}")
            continue
        model_id = str(entry.get("model_id") or "").strip()
        if not model_id:
            problems.append(f"model_id manquant : {entry!r}")
            continue
        api = str(entry.get("api") or API_OPENROUTER).strip().lower()
        if api not in SUPPORTED_APIS:
            problems.append(f"API inconnue « {api} » pour {model_id}")
            continue
        key = slugify_key(str(entry.get("provider") or "")) or slugify_key(model_id)
        if key in models:
            problems.append(f"clé en double « {key} »")
            continue
        display_name = str(entry.get("display_name") or "").strip() or model_id
        models[key] = ModelConfig(key=key, api=api, model_id=model_id, display_name=display_name)
    return models, problems


@dataclass
class ModelAnswer:
    """One model's answer (or failure) to a prompt."""

    model: ModelConfig
    content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    latency: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.content)


@dataclass(frozen=True)
class HistoryMessage:
    """A channel message reduced to what the prompt needs (built by the extension)."""

    author: str
    content: str
    is_self: bool = False
    is_comparison: bool = False
    has_attachments: bool = False
    reply_to: str | None = None


@dataclass
class ComparisonAnswer:
    key: str
    api: str
    model_id: str
    display_name: str
    content: str

    def to_mongo(self) -> dict[str, str]:
        return {
            "key": self.key,
            "api": self.api,
            "model_id": self.model_id,
            "display_name": self.display_name,
            "content": self.content,
        }

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> ComparisonAnswer:
        return cls(
            key=str(doc.get("key", "")),
            api=str(doc.get("api", API_OPENROUTER)),
            model_id=str(doc.get("model_id", "")),
            display_name=str(doc.get("display_name", doc.get("key", ""))),
            content=str(doc.get("content", "")),
        )


@dataclass
class Comparison:
    """A posted side-by-side comparison and its votes."""

    question: str
    asker_id: str
    channel_id: str
    answers: list[ComparisonAnswer]
    closes_at: datetime
    guild_id: str | None = None
    message_ids: list[str] = field(default_factory=list)
    votes: dict[str, str] = field(default_factory=dict)
    closed: bool = False
    created_at: datetime | None = None
    id: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "asker_id": self.asker_id,
            "channel_id": self.channel_id,
            "guild_id": self.guild_id,
            "answers": [a.to_mongo() for a in self.answers],
            "message_ids": list(self.message_ids),
            "votes": dict(self.votes),
            "closes_at": self.closes_at,
            "closed": self.closed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> Comparison:
        return cls(
            id=str(doc["_id"]) if doc.get("_id") is not None else None,
            question=str(doc.get("question", "")),
            asker_id=str(doc.get("asker_id", "")),
            channel_id=str(doc.get("channel_id", "")),
            guild_id=doc.get("guild_id"),
            answers=[ComparisonAnswer.from_mongo(a) for a in doc.get("answers") or []],
            message_ids=[str(m) for m in doc.get("message_ids") or []],
            votes={str(k): str(v) for k, v in (doc.get("votes") or {}).items()},
            closes_at=doc.get("closes_at") or datetime.min,
            closed=bool(doc.get("closed", False)),
            created_at=doc.get("created_at"),
        )
