"""Unit tests for ``features.rss.parser``."""

import pytest

from features.rss.parser import parse_feed, strip_html
from src.core.errors import IntegrationError

RSS_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Free Games Weekly</title>
    <link>https://example.com</link>
    <description>Test feed</description>
    <item>
      <title>Free Game: <b>Frostpunk</b></title>
      <link>https://example.com/frostpunk</link>
      <guid>fg-001</guid>
      <pubDate>Fri, 24 Apr 2026 10:00:00 +0000</pubDate>
      <description>Grab &lt;i&gt;Frostpunk&lt;/i&gt; on Epic until Friday.</description>
    </item>
    <item>
      <title>Free Game: Death Stranding</title>
      <link>https://example.com/ds</link>
      <guid>fg-002</guid>
    </item>
  </channel>
</rss>
"""

ATOM_SAMPLE = """\
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Subreddit r/example</title>
  <link href="https://example.com"/>
  <updated>2026-04-25T10:00:00Z</updated>
  <id>tag:example.com</id>
  <entry>
    <title>Hello there</title>
    <link href="https://example.com/post/1"/>
    <id>tag:example.com,2026:post-1</id>
    <updated>2026-04-25T09:00:00Z</updated>
    <author><name>op</name></author>
    <summary>A nice post.</summary>
  </entry>
</feed>
"""


def test_parse_rss_extracts_two_entries() -> None:
    entries = parse_feed(RSS_SAMPLE)
    assert len(entries) == 2
    assert entries[0].entry_id == "fg-001"
    assert entries[0].title == "Free Game: Frostpunk"
    assert entries[0].link == "https://example.com/frostpunk"
    assert "Frostpunk" in entries[0].summary
    assert entries[0].published is not None


def test_parse_atom_extracts_author_and_summary() -> None:
    entries = parse_feed(ATOM_SAMPLE)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entry_id == "tag:example.com,2026:post-1"
    assert entry.title == "Hello there"
    assert entry.author == "op"
    assert entry.summary == "A nice post."


def test_strip_html_decodes_entities_and_truncates() -> None:
    out = strip_html("<p>Hello &amp; <i>world</i></p>", max_length=80)
    assert out == "Hello & world"
    long = "x" * 1000
    out = strip_html(long, max_length=20)
    assert len(out) == 20
    assert out.endswith("…")


def test_strip_html_handles_none_and_empty() -> None:
    assert strip_html(None) == ""
    assert strip_html("") == ""
    assert strip_html("   ") == ""


def test_parse_feed_raises_on_garbage() -> None:
    with pytest.raises(IntegrationError):
        parse_feed("this is not xml")


def test_parse_feed_raises_on_unknown_root() -> None:
    with pytest.raises(IntegrationError):
        parse_feed("<html><body>nope</body></html>")
