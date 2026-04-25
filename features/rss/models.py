"""Pydantic models for the RSS feed feature."""

from datetime import datetime

from pydantic import BaseModel, Field


class RssEntry(BaseModel):
    """A normalized entry pulled from an RSS or Atom feed.

    The parser collapses provider-specific quirks (Atom vs. RSS, ``<id>`` vs.
    ``<link>``, escaped HTML in titles) into this flat shape so downstream code
    only deals with strings.

    ``image_url`` is the first usable image found, in this order:
    ``<media:thumbnail>`` → ``<media:content medium="image">`` →
    ``<enclosure type="image/*">`` → first ``<img src>`` inside the
    summary/content HTML. Empty string when none is available.
    """

    entry_id: str
    title: str
    link: str
    summary: str = ""
    author: str = ""
    published: datetime | None = None
    image_url: str = ""


class RssFeedState(BaseModel):
    """Per-feed bookkeeping persisted to MongoDB.

    ``seen_ids`` is a bounded list (most recent first) used to dedupe entries
    across polls. ``initialized`` is set after the first successful fetch so
    the very first poll's entries don't get blasted to the channel.
    """

    feed_id: str = Field(alias="_id")
    seen_ids: list[str] = Field(default_factory=list)
    initialized: bool = False
    last_poll_at: datetime | None = None
    last_error: str | None = None

    model_config = {"populate_by_name": True}
