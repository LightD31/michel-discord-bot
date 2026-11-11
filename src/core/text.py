"""Pure-Python text / number / duration helpers — no Discord dependency.

These were previously scattered across ``src/utils.py`` and ``src/helpers.py``.
Grouping them here makes them easy to reuse from feature code that doesn't
import ``interactions``.
"""

from __future__ import annotations

import random
import re
import string
from typing import Any

import emoji

# ---------------------------------------------------------------------------
# Duration & number formatting
# ---------------------------------------------------------------------------


def milliseconds_to_string(duration_ms: float | str) -> str:
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


def format_number(num: float) -> str:
    """Format a number with a k suffix for thousands."""
    if num >= 1000:
        return f"{num / 1000:.1f}k"
    return str(num)


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------


def take_within_budget(
    entries: list[str], budget: int, separator: str = ", "
) -> tuple[list[str], int]:
    """Take entries in order while their joined length fits ``budget``.

    Discord caps an embed field at 1024 characters and a whole message's
    embeds at 6000, so a list of arbitrary length has to be cut somewhere.
    Cutting by entry count is the wrong axis — it either wastes the budget or
    overruns it depending on how long the names happen to be.

    Returns the entries kept and the characters they cost, counting the
    separators that will join them.
    """
    kept: list[str] = []
    used = 0
    for entry in entries:
        cost = len(entry) + (len(separator) if kept else 0)
        if used + cost > budget:
            break
        kept.append(entry)
        used += cost
    return kept, used


# Discord's cap on a message's content.
MESSAGE_LIMIT = 2000
# Discord's limits on the parts of an embed (a longer one is refused with a 400).
EMBED_TITLE_LIMIT = 256
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_FOOTER_LIMIT = 2048
EMBED_AUTHOR_LIMIT = 256

_USER_MENTION = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION = re.compile(r"<@&(\d+)>")


def clip(text: str, limit: int = MESSAGE_LIMIT) -> str:
    """*text* cut to *limit* characters, ending with "…" when it was cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def mentions_in(text: str) -> tuple[list[str], list[str], bool]:
    """The user ids, role ids, and whether ``@everyone``/``@here``, written in *text*.

    Used to let a message ping what a server admin wrote in its template and
    nothing a third party's text (a feed title, a member's input) adds to it.
    """
    users = list(dict.fromkeys(_USER_MENTION.findall(text)))
    roles = list(dict.fromkeys(_ROLE_MENTION.findall(text)))
    return users, roles, "@everyone" in text or "@here" in text


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


def search_dict_by_sentence(my_dict: dict[Any, Any], sentence: str) -> Any:
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


# ---------------------------------------------------------------------------
# Templates written by server admins
# ---------------------------------------------------------------------------


class TemplateError(ValueError):
    """A configured template uses more than plain ``{name}`` placeholders."""


class _Unknown:
    """A placeholder the template names but no value fills: rendered as written."""

    def __init__(self, name: str) -> None:
        self.text = "{" + name + "}"


class _TemplateFormatter(string.Formatter):
    """``str.format`` limited to ``{name}``: no format spec, conversion, attribute or index.

    A server admin writes these templates and the bot renders them for every
    server: ``{mention:>2000000000}`` would allocate gigabytes and take the
    process down, ``{title.__class__}`` would walk object attributes.
    """

    def __init__(self, *, keep_unknown: bool) -> None:
        super().__init__()
        self._keep_unknown = keep_unknown

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> Any:
        if not field_name.isidentifier():
            raise TemplateError(f"{{{field_name}}} : seules les variables {{nom}} sont permises")
        return super().get_field(field_name, args, kwargs)

    def get_value(self, key: int | str, args: Any, kwargs: Any) -> Any:
        if key in kwargs:
            return kwargs[key]
        if self._keep_unknown:
            return _Unknown(str(key))
        raise KeyError(key)

    def convert_field(self, value: Any, conversion: str | None) -> Any:
        if conversion is not None:
            raise TemplateError(f"!{conversion} n'est pas permis dans un modèle")
        return value

    def format_field(self, value: Any, format_spec: str) -> str:
        if format_spec:
            raise TemplateError(f":{format_spec} n'est pas permis dans un modèle")
        return value.text if isinstance(value, _Unknown) else str(value)


def format_template(template: str, *, keep_unknown: bool = False, **values: Any) -> str:
    """Fill a template a server admin configured: ``{name}`` placeholders only.

    Raises :class:`TemplateError` (a ``ValueError``) for a format spec, a
    conversion, an attribute or an index; ``KeyError`` for a name *values*
    doesn't have, unless *keep_unknown* leaves it as written; ``ValueError``
    for a malformed template (a stray brace).
    """
    return _TemplateFormatter(keep_unknown=keep_unknown).vformat(template, (), values)


def template_problem(template: str) -> str | None:
    """Why a dashboard save should refuse *template*, in French, or None.

    Only what :func:`format_template` refuses is reported. Unknown names and
    stray braces are left alone: those templates are sent as written, as before.
    """
    try:
        fields = [
            (name, spec, conversion)
            for _, name, spec, conversion in string.Formatter().parse(template)
            if name is not None
        ]
    except ValueError:
        return None
    for name, spec, conversion in fields:
        if not name.isidentifier():
            return f"« {{{name}}} » : seules les variables simples comme {{mention}} sont permises."
        if spec or conversion:
            return f"« {{{name}…}} » : les formats (après « : » ou « ! ») ne sont pas permis."
    return None


def pick_weighted_message(
    config: dict[str, Any],
    list_key: str,
    default: str,
    **format_kwargs: Any,
) -> str:
    """Pick a random message from ``config[list_key]`` using weights, then format it.

    Entries are ``{"text": str, "weight": number}`` objects; anything else is
    skipped. The texts come from guild admins, so a mistake in one never stops
    the message: an unknown ``{placeholder}`` stays as written, and a text that
    isn't a valid format string is sent unformatted.

    Example::

        msg = pick_weighted_message(
            srv_cfg,
            "birthdayMessageList",
            "Joyeux anniversaire {mention} !",
            mention=member.mention, age=age,
        )
    """
    entries = config.get(list_key) or []
    texts: list[str] = []
    weights: list[float] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text:
            continue
        try:
            weight = max(float(entry.get("weight", 1)), 0.0)
        except (TypeError, ValueError):
            weight = 1.0
        texts.append(text)
        weights.append(weight)
    if not texts:
        texts, weights = [default], [1.0]
    if not any(weights):
        # random.choices raises when every weight is 0 — treat as all-equal.
        weights = [1.0] * len(texts)
    chosen: str = random.choices(texts, weights=weights)[0]
    try:
        return format_template(chosen, keep_unknown=True, **format_kwargs)
    except (ValueError, KeyError):
        return chosen


__all__ = [
    "EMBED_AUTHOR_LIMIT",
    "EMBED_DESCRIPTION_LIMIT",
    "EMBED_FIELD_NAME_LIMIT",
    "EMBED_FIELD_VALUE_LIMIT",
    "EMBED_FOOTER_LIMIT",
    "EMBED_TITLE_LIMIT",
    "MESSAGE_LIMIT",
    "clip",
    "escape_md",
    "extract_answer",
    "TemplateError",
    "format_number",
    "format_template",
    "mentions_in",
    "milliseconds_to_string",
    "pick_weighted_message",
    "remove_punctuation",
    "sanitize_content",
    "search_dict_by_sentence",
    "template_problem",
]
