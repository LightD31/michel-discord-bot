"""AI answers feature — prompt building, rendering, stats and persistence for /ask.

No ``interactions`` imports: the extension (``extensions/compareai``) gathers
Discord data into plain types (:class:`HistoryMessage`) and sends what the
renderers return.
"""

from features.ai.client import AiClient
from features.ai.mentions import Cooldown, MentionFacts, RecentAnswers, mention_question
from features.ai.models import (
    DEFAULT_MODELS,
    MAX_COMPARED_MODELS,
    Comparison,
    ComparisonAnswer,
    HistoryMessage,
    ModelAnswer,
    ModelConfig,
    parse_models,
    slugify_key,
)
from features.ai.prompt import (
    DEFAULT_PERSONA,
    build_history,
    build_system_prompt,
    extract_response,
    participants_from,
    question_turn,
    replace_user_mentions,
    strip_user_mention,
)
from features.ai.render import (
    attach_footer,
    question_header,
    render_comparison_body,
    render_comparison_footer,
    render_single,
    split_message,
    tally,
    winners,
)
from features.ai.repository import AiComparisonRepository
from features.ai.settings import (
    AiGlobalSettings,
    GuildAiSettings,
    pick_compare_models,
    resolve_default_model,
    usable_models,
)
from features.ai.stats import ModelStats, compute_model_stats

__all__ = [
    "DEFAULT_MODELS",
    "DEFAULT_PERSONA",
    "MAX_COMPARED_MODELS",
    "AiClient",
    "AiComparisonRepository",
    "AiGlobalSettings",
    "Comparison",
    "ComparisonAnswer",
    "Cooldown",
    "GuildAiSettings",
    "HistoryMessage",
    "MentionFacts",
    "ModelAnswer",
    "ModelConfig",
    "ModelStats",
    "RecentAnswers",
    "attach_footer",
    "build_history",
    "build_system_prompt",
    "compute_model_stats",
    "extract_response",
    "mention_question",
    "parse_models",
    "participants_from",
    "pick_compare_models",
    "question_header",
    "question_turn",
    "render_comparison_body",
    "render_comparison_footer",
    "render_single",
    "replace_user_mentions",
    "resolve_default_model",
    "slugify_key",
    "split_message",
    "strip_user_mention",
    "tally",
    "usable_models",
    "winners",
]
