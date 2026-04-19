"""
Shared constants, configuration, dataclasses, and utilities for the CompareAI
extension package.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleIA")
class IAConfig(SchemaBase):
    __label__ = "Intelligence Artificielle"
    __description__ = "Comparaison de modèles IA via OpenRouter."
    __icon__ = "🤖"
    __category__ = "Outils"

    enabled: bool = enabled_field()


# =============================================================================
# Logging & config
# =============================================================================

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleIA")

# =============================================================================
# Constants
# =============================================================================

DISCORD_MESSAGE_LIMIT = 1900
VOTE_TIMEOUT_SECONDS = 60
MAX_API_RETRIES = 3
API_TIMEOUT_SECONDS = 10
MAX_RESPONSE_TOKENS = 500
CONVERSATION_HISTORY_LIMIT = 10
DATA_DIR = Path("data")
VOTES_FILE = DATA_DIR / "responses.txt"

# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class ModelConfig:
    """Configuration for an AI model."""

    provider: str
    model_id: str
    display_name: str


@dataclass
class ModelResponse:
    """Container for a model's response."""

    provider: str
    content: str
    raw_response: object = None


@dataclass
class UserInfo:
    """Information about a user in the conversation."""

    user_id: int
    username: str
    display_name: str
    is_author: bool = False


@dataclass
class ModelPricing:
    """Pricing information for a model."""

    input_cost_per_token: float
    output_cost_per_token: float

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate total cost for the given token counts."""
        return self.input_cost_per_token * input_tokens + self.output_cost_per_token * output_tokens


# =============================================================================
# Model registry
# =============================================================================

_DEFAULT_MODELS: list[dict[str, str]] = [
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
        "provider": "qwen",
        "model_id": "qwen/qwen3-vl-235b-a22b-instruct",
        "display_name": "Qwen3 235B A22B Instruct",
    },
    {
        "provider": "gemini",
        "model_id": "google/gemini-3-pro-preview",
        "display_name": "Google Gemini 3 Pro Preview",
    },
    {
        "provider": "xai",
        "model_id": "x-ai/grok-4.1-fast:free",
        "display_name": "X-AI Grok 4.1 Fast",
    },
]


def _load_models_from_defaults() -> dict[str, ModelConfig]:
    """Build AVAILABLE_MODELS from the hardcoded defaults."""
    return {
        entry["provider"]: ModelConfig(
            provider=entry["provider"],
            model_id=entry["model_id"],
            display_name=entry["display_name"],
        )
        for entry in _DEFAULT_MODELS
    }


def _load_models_from_config() -> dict[str, ModelConfig]:
    """Load AI models from the global config, falling back to defaults."""
    models_raw = config.get("OpenRouter", {}).get("models", _DEFAULT_MODELS)
    models: dict[str, ModelConfig] = {}
    for entry in models_raw:
        provider = entry.get("provider", "")
        model_id = entry.get("model_id", "")
        display_name = entry.get("display_name", provider)
        if provider and model_id:
            models[provider] = ModelConfig(
                provider=provider,
                model_id=model_id,
                display_name=display_name,
            )
        else:
            logger.warning(f"Skipping invalid model entry: {entry}")
    if not models:
        logger.error("No valid models configured, falling back to defaults")
        return _load_models_from_defaults()
    logger.info(f"Loaded {len(models)} AI models from config: {list(models.keys())}")
    return models


AVAILABLE_MODELS: dict[str, ModelConfig] = _load_models_from_config()

# Number of models to compare per question (from config or default)
MODELS_TO_COMPARE = config.get("OpenRouter", {}).get("modelsToCompare", 3)

# =============================================================================
# Helpers
# =============================================================================


class VoteManager:
    """Handles vote storage and counting."""

    def __init__(self, votes_file: Path = VOTES_FILE):
        self.votes_file = votes_file
        self._ensure_data_dir()

    def _ensure_data_dir(self) -> None:
        """Ensure the data directory exists."""
        self.votes_file.parent.mkdir(parents=True, exist_ok=True)

    def save_vote(self, provider: str) -> bool:
        """Save a vote to the file. Returns True on success."""
        try:
            with open(self.votes_file, "a", encoding="utf-8") as f:
                f.write(f"{provider}\n")
            return True
        except OSError as e:
            logger.error(f"Error saving vote: {e}")
            return False

    def count_votes(self) -> dict[str, int]:
        """Count all votes by provider."""
        counts = {provider: 0 for provider in AVAILABLE_MODELS}

        if not self.votes_file.exists():
            return counts

        try:
            with open(self.votes_file, encoding="utf-8") as f:
                for line in f:
                    provider = line.strip()
                    if provider in counts:
                        counts[provider] += 1
        except OSError as e:
            logger.error(f"Error counting votes: {e}")

        return counts


class MessageSplitter:
    """Utility for splitting long Discord messages."""

    @staticmethod
    def split_message(content: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
        """
        Split a message into chunks that fit within Discord's limit.

        Attempts to split at paragraph boundaries, then line breaks, then spaces.
        """
        if len(content) <= limit:
            return [content]

        messages = []
        paragraphs = content.split("\n\n")
        current_chunk = ""

        for paragraph in paragraphs:
            if len(paragraph) > limit:
                if current_chunk:
                    messages.append(current_chunk)
                    current_chunk = ""
                messages.extend(MessageSplitter._split_long_text(paragraph, limit))
            elif len(current_chunk) + len(paragraph) + 2 > limit:
                messages.append(current_chunk)
                current_chunk = paragraph
            else:
                current_chunk = f"{current_chunk}\n\n{paragraph}" if current_chunk else paragraph

        if current_chunk:
            messages.append(current_chunk)

        return messages

    @staticmethod
    def _split_long_text(text: str, limit: int) -> list[str]:
        """Split text that exceeds the limit."""
        chunks = []

        while text:
            if len(text) <= limit:
                chunks.append(text)
                break

            cut_index = MessageSplitter._find_split_point(text, limit)
            chunks.append(text[:cut_index])
            text = text[cut_index:].lstrip()

        return chunks

    @staticmethod
    def _find_split_point(text: str, limit: int) -> int:
        """Find the best point to split text."""
        newline_idx = text[:limit].rfind("\n")
        if newline_idx > limit // 2:
            return newline_idx + 1

        space_idx = text[:limit].rfind(" ")
        if space_idx > limit - 100:
            return space_idx + 1

        return limit
