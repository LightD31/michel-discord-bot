"""Deprecated: the utilities moved under :mod:`src.core` and :mod:`src.discord_ext`.

Re-export shim kept for one release so ``from src.utils import …`` keeps
working. New code should import directly from the destination module:

- ``fetch`` → :mod:`src.core.http`
- ``load_config`` / ``save_config`` / ``load_discord2name`` → :mod:`src.core.config`
- Text / number / duration helpers → :mod:`src.core.text`
- ``create_dynamic_image`` → :mod:`src.core.images`
- ``CustomPaginator`` / ``format_poll`` / ``name_cache`` → :mod:`src.discord_ext.paginator`
"""

from src.core.config import (  # noqa: F401 — re-exported for backward compat
    load_config,
    load_discord2name,
    save_config,
)
from src.core.http import fetch  # noqa: F401
from src.core.images import create_dynamic_image  # noqa: F401
from src.core.text import (  # noqa: F401
    escape_md,
    extract_answer,
    format_number,
    milliseconds_to_string,
    remove_punctuation,
    sanitize_content,
    search_dict_by_sentence,
)
from src.discord_ext.paginator import (  # noqa: F401
    CustomPaginator,
    format_poll,
    name_cache,
)

__all__ = [
    "CustomPaginator",
    "create_dynamic_image",
    "escape_md",
    "extract_answer",
    "fetch",
    "format_number",
    "format_poll",
    "load_config",
    "load_discord2name",
    "milliseconds_to_string",
    "name_cache",
    "remove_punctuation",
    "sanitize_content",
    "save_config",
    "search_dict_by_sentence",
]
