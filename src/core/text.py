"""Pure-Python text / number / duration helpers — no Discord dependency.

These were previously scattered across ``src/utils.py`` and ``src/helpers.py``.
Grouping them here makes them easy to reuse from feature code that doesn't
import ``interactions``.
"""

from __future__ import annotations

import random
import re
import string

import emoji

# ---------------------------------------------------------------------------
# Duration & number formatting
# ---------------------------------------------------------------------------

def milliseconds_to_string(duration_ms) -> str:
    """Convert milliseconds to a French human-readable duration string."""
    duration_ms = int(duration_ms)
    seconds = duration_ms / 1000
    days = seconds // 86400
    seconds %= 86400
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return (
        f"{int(days)} jour(s) {int(hours):02d} heure(s) "
        f"{int(minutes):02d} minute(s) et {int(seconds):02d} seconde(s)"
    )


def format_number(num) -> str:
    """Format a number with a k suffix for thousands."""
    if num >= 1000:
        return f"{num / 1000:.1f}k"
    return str(num)


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def escape_md(text: str) -> str:
    """Escape Markdown special characters."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", text)


def sanitize_content(content: str) -> str:
    """Remove custom emojis, unicode emojis, and mentions from content."""
    content = re.sub(r"<:\w*:\d*>", "", content)
    content = emoji.replace_emoji(content, " ")
    content = re.sub(r"<@\d*>", "", content)
    return content


def remove_punctuation(input_string: str) -> str:
    """Remove all punctuation from the input string."""
    translator = str.maketrans("", "", string.punctuation)
    return input_string.translate(translator).strip()


def search_dict_by_sentence(my_dict: dict, sentence: str):
    """Return the first value whose key (or any tuple-key element) matches a word in *sentence*."""
    words = set(sentence.lower().split())
    for key, value in my_dict.items():
        if isinstance(key, tuple):
            key_lower = tuple(k.lower() for k in key)
            if any(word in key_lower for word in words):
                return value
        else:
            if key.lower() in words:
                return value
    return None


def extract_answer(text: str) -> str | None:
    """Extract content between ``<answer>`` tags."""
    pattern = r"<answer>(.*?)</answer>"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Weighted random message picker
# ---------------------------------------------------------------------------

def pick_weighted_message(
    config: dict,
    list_key: str,
    weights_key: str,
    default: str,
    **format_kwargs,
) -> str:
    """Pick a random message from ``config[list_key]`` using weights, then format it.

    Example::

        msg = pick_weighted_message(
            srv_cfg,
            "birthdayMessageList", "birthdayMessageWeights",
            "Joyeux anniversaire {mention} !",
            mention=member.mention, age=age,
        )
    """
    messages = config.get(list_key, [default])
    weights = config.get(weights_key, [1] * len(messages))
    chosen = random.choices(messages, weights=weights)[0]
    return chosen.format(**format_kwargs)


__all__ = [
    "escape_md",
    "extract_answer",
    "format_number",
    "milliseconds_to_string",
    "pick_weighted_message",
    "remove_punctuation",
    "sanitize_content",
    "search_dict_by_sentence",
]
