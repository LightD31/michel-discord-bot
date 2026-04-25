"""RSS / Atom feed feature — pure parser, models, and persistence layer.

The Discord-facing extension lives in ``extensions/rss``. This package only
deals with feed normalization and per-guild dedupe state.

The async :func:`fetch_feed` helper lives in :mod:`features.rss.network` and
``RssRepository`` in :mod:`features.rss.repository` — both are exposed via
``__getattr__`` so the top-level import only pulls in the pure parser. Tests
of the parsing logic don't need ``aiohttp`` or ``motor``.
"""

from features.rss.models import RssEntry, RssFeedState
from features.rss.parser import normalize_entry, parse_feed, strip_html

MAX_SEEN_IDS = 200  # mirror of ``features.rss.repository.MAX_SEEN_IDS``

__all__ = [
    "MAX_SEEN_IDS",
    "RssEntry",
    "RssFeedState",
    "RssRepository",
    "normalize_entry",
    "parse_feed",
    "strip_html",
]


def __getattr__(name: str):  # noqa: D401 — module-level dunder
    if name == "RssRepository":
        from features.rss.repository import RssRepository

        return RssRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
