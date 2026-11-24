"""CLAUDE.md stays short, and what it points to exists.

It is read in full at the start of every session: one line per rule, each
naming the test that enforces it or the file under docs/conventions/ that
explains it. A rule that grows a paragraph belongs in that file.
"""

import re

from source_scan import ROOT

CLAUDE = ROOT / "CLAUDE.md"
CONVENTIONS = ROOT / "docs" / "conventions"
MAX_BYTES = 12 * 1024


def test_claude_md_stays_under_12_kb():
    size = len(CLAUDE.read_bytes())
    assert size <= MAX_BYTES, f"CLAUDE.md is {size} bytes: move the detail to docs/conventions/"


def test_every_test_it_names_exists():
    named = set(re.findall(r"tests/(test_\w+\.py)", CLAUDE.read_text(encoding="utf-8")))
    assert named, "CLAUDE.md names no test"
    missing = sorted(n for n in named if not (ROOT / "tests" / n).is_file())
    assert missing == [], (
        "CLAUDE.md names a test that no longer exists: point the rule at its new guard"
    )


def test_every_convention_file_is_named_and_exists():
    text = CLAUDE.read_text(encoding="utf-8")
    named = set(re.findall(r"\b([a-z-]+)\.md\b", text)) - {"CLAUDE", "VOIX", "RUNBOOK", "Privacy"}
    files = {p.stem for p in CONVENTIONS.glob("*.md")} - {"README"}
    assert named - files - {"README"} == set(), "named but missing"
    assert files - named == set(), "a convention file CLAUDE.md doesn't point to"


def test_the_index_links_resolve():
    index = (CONVENTIONS / "README.md").read_text(encoding="utf-8")
    links = set(re.findall(r"\]\(([a-z-]+\.md)\)", index))
    assert links and all((CONVENTIONS / link).is_file() for link in links)
