"""The dashboard's colours are readable: each text/background pair passes WCAG AA (P3).

White on the green "Sauvegarder" button was 3.2:1, white on red 3.8:1 and the
breadcrumb link 3.7:1. The theme now keeps two reds and two greens: the plain
ones for text and outlines, the ``-strong`` ones for the fills white text sits
on. This test reads the colours from ``index.html``, so a new shade that
doesn't pass fails here rather than in a screenshot.
"""

import re

import pytest
from source_scan import ROOT

INDEX = (ROOT / "src/webui/static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "src/webui/static/app.js").read_text(encoding="utf-8")
ROOT_VARS = dict(
    re.findall(
        r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", INDEX.split(":root {", 1)[1].split("}", 1)[0]
    )
)
AA = 4.5


def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted([luminance(a), luminance(b)], reverse=True)
    return (high + 0.05) / (low + 0.05)


def color(name: str) -> str:
    return "#ffffff" if name == "white" else ROOT_VARS[name]


@pytest.mark.parametrize(
    ("text", "background"),
    [
        ("white", "success-strong"),  # .btn-success, the success toast
        ("white", "danger-strong"),  # .btn-danger, the error toast, "requis"
        ("white", "accent"),  # .btn-primary, the login button
        ("link", "bg-primary"),  # breadcrumbs, links in a page
        ("link", "bg-card"),  # links in a card (a member in a case)
        ("danger", "bg-card"),  # "Révoquer", a warning in red
        ("danger", "bg-primary"),
        ("text-secondary", "bg-card"),
        ("text-muted", "bg-card"),
        ("warning", "bg-card"),  # a picker's warning
    ],
)
def test_text_is_readable_on_its_background(text, background):
    ratio = contrast(color(text), color(background))
    assert ratio >= AA, f"--{text} sur --{background} : {ratio:.2f}:1"


@pytest.mark.parametrize(
    "rule",
    [
        r"\.btn-success \{\s*background: var\(--success-strong\)",
        r"\.btn-danger \{\s*background: var\(--danger-strong\)",
        r"\.toast\.success \{ background: var\(--success-strong\)",
        r"\.toast\.error \{ background: var\(--danger-strong\)",
    ],
)
def test_white_text_sits_on_the_strong_fills(rule):
    assert re.search(rule, INDEX), rule


def test_every_colour_the_css_names_is_defined():
    """An undefined ``var(--x)`` with no fallback drops the whole declaration.

    ``--border-color`` was one: the ✎ buttons of the pickers had no border.
    """
    used = set(re.findall(r"var\(--([a-z0-9-]+)\)", INDEX + APP))
    defined = set(re.findall(r"--([a-z0-9-]+):", INDEX))
    assert sorted(used - defined) == []
