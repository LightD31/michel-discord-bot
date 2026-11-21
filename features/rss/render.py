"""An RSS entry rendered as the bot posts it in text mode.

Shared by the extension (``extensions/rss``) and the dashboard's "Tester ce
flux" (``src/webui/routes/rss.py``), so the test shows what would be posted.
Pure: no Discord and no network.
"""

from __future__ import annotations

from features.rss.models import RssEntry
from src.core.text import format_template

DEFAULT_TEMPLATE = "**{label}** — [{title}]({link})"


def published_tokens(entry: RssEntry) -> dict[str, str]:
    """Return Discord timestamp tags for ``entry.published``.

    Each style mirrors a Discord timestamp format identifier — clients render
    them in the viewer's locale and timezone. Empty strings if the feed entry
    didn't expose a publish/updated date.
    """
    if not entry.published:
        return {
            "published": "",
            "published_short": "",
            "published_long": "",
            "published_relative": "",
            "published_date": "",
            "published_iso": "",
        }
    ts = int(entry.published.timestamp())
    return {
        "published": f"<t:{ts}:F>",
        "published_short": f"<t:{ts}:f>",
        "published_long": f"<t:{ts}:F>",
        "published_relative": f"<t:{ts}:R>",
        "published_date": f"<t:{ts}:D>",
        "published_iso": entry.published.isoformat(),
    }


def format_entry(template: str, *, label: str, entry: RssEntry, fallback: str) -> str:
    """Fill the guild's template with the entry; a template that can't be filled gives *fallback*.

    ``{name}`` placeholders only (:func:`format_template`): any other mistake
    (``{title:d}``, ``{title.x}``, an unknown name) falls back instead of
    failing the post, which left the entry unseen and retried forever.
    """
    try:
        return format_template(
            template,
            title=entry.title,
            link=entry.link,
            summary=entry.summary,
            author=entry.author,
            label=label,
            image=entry.image_url,
            **published_tokens(entry),  # type: ignore[arg-type]  # only str values, never keep_unknown
        )
    except (KeyError, ValueError):
        return fallback


def render_text(feed_id: str, feed_cfg: dict, entry: RssEntry) -> str:
    """Render a plain-text/markdown message for ``mode="text"``.

    Clipped to Discord's 2000-char message-content limit; the parser caps the
    summary near the embed limit so the same summary fed into a text template
    can occasionally need clipping here.
    """
    label = feed_cfg.get("label") or feed_id
    template = feed_cfg.get("template") or DEFAULT_TEMPLATE
    fallback = DEFAULT_TEMPLATE.format(
        title=entry.title, link=entry.link, label=label, summary="", author="", image=""
    )
    rendered = format_entry(template, label=label, entry=entry, fallback=fallback)
    if len(rendered) > 2000:
        rendered = rendered[:1999].rstrip() + "…"
    return rendered


__all__ = ["DEFAULT_TEMPLATE", "format_entry", "published_tokens", "render_text"]
