"""The dashboard SPA runs under a CSP without inline code (T09).

``security_headers.CONTENT_SECURITY_POLICY`` allows scripts from the
dashboard's own files only, so an ``onclick="…"`` attribute or an inline
``<script>`` would simply not run. These checks keep the markup that way and
make sure every action a control names exists in the SPA's allowlist.
"""

import re
from pathlib import Path

import pytest

from src.webui.security_headers import CHARTJS_URL

STATIC = Path(__file__).resolve().parents[1] / "src" / "webui" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")

# An on…= attribute, quoted or not, in markup or built by the script.
INLINE_HANDLER = re.compile(r"""[\s"'/]on[a-z]+\s*=\s*["'`\\\w(]""", re.IGNORECASE)
SET_HANDLER = re.compile(r"""setAttribute\(\s*["'`]on""", re.IGNORECASE)
# A <script> without a src attribute (data-src="…" isn't one).
INLINE_SCRIPT = re.compile(r"<script(?![^>]*(?<![\w-])src\s*=)[^>]*>", re.IGNORECASE)
# Code from a string: eval, Function (with or without new), a string timer.
STRING_CODE = re.compile(
    r"""\beval\s*\(|\bFunction\s*\(|\bset(Timeout|Interval)\s*\(\s*["'`]"""
)
ACTION_ATTR = re.compile(r'data-(click|change|input|mousedown|keydown)="([\w-]+)"')


def _allowlisted_actions(app: str = APP) -> set[str]:
    """The keys of ``const ACTIONS = {…}``: top-level entries only, not parameters."""
    start = app.index("const ACTIONS = {") + len("const ACTIONS = {")
    names, depth, entry = set(), 0, ""
    code = re.sub(r"//[^\n]*", "", app[start:])
    for char in code:
        if depth == 0 and char in ",}":
            key = re.match(r"\s*(?:'([\w-]+)'|([A-Za-z_]\w*))\s*(?::|$)", entry)
            if key:
                names.add(key.group(1) or key.group(2))
            entry = ""
            if char == "}":
                break
            continue
        depth += char in "([{"
        depth -= char in ")]}"
        entry += char if depth == 0 or char in "([{" else ""
    return names


def test_no_inline_script_or_handler_in_the_page():
    assert INLINE_SCRIPT.findall(INDEX) == []
    assert '<script src="/static/app.js"></script>' in INDEX
    assert INLINE_HANDLER.findall(INDEX) == []


def test_no_inline_handler_in_the_markup_the_script_builds():
    assert INLINE_HANDLER.findall(APP) == []
    assert SET_HANDLER.findall(APP) == []
    for text in (APP, INDEX):
        assert "javascript:" not in text.lower()
    assert STRING_CODE.findall(APP) == []


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (INLINE_SCRIPT, '<script data-src="x">alert(1)</script>'),
        (INLINE_SCRIPT, "<SCRIPT>alert(1)</SCRIPT>"),
        (INLINE_HANDLER, "<button onclick=save()>"),
        (INLINE_HANDLER, '<img src=x onerror="go()">'),
        (INLINE_HANDLER, "<svg/onload=go()>"),
        (SET_HANDLER, "el.setAttribute('onclick', code)"),
        (STRING_CODE, "setTimeout('go()', 10)"),
        (STRING_CODE, "Function('return 1')()"),
        (STRING_CODE, "new Function('x')"),
    ],
)
def test_the_checks_catch(pattern, text):
    assert pattern.search(text)


@pytest.mark.parametrize(
    ("pattern", "text"),
    [
        (INLINE_SCRIPT, '<script src="/static/app.js"></script>'),
        (INLINE_HANDLER, "img.onload = () => draw();"),
        (INLINE_HANDLER, '<div data-click="save">'),
        (STRING_CODE, "setTimeout(() => el.remove(), 4000)"),
    ],
)
def test_the_checks_let_the_allowed_forms_through(pattern, text):
    assert not pattern.search(text)


def test_the_allowlist_holds_action_names_not_parameter_names():
    app = (
        "const ACTIONS = {\n"
        "    saveForm,\n"
        "    'remove-closest': (selector, el) => el.closest(selector)?.remove(),\n"
        "    other: (a, { b }) => [a, b],\n"
        "};"
    )
    assert _allowlisted_actions(app) == {"saveForm", "remove-closest", "other"}
    assert "selector" not in _allowlisted_actions()


def test_every_action_a_control_names_is_allowlisted():
    named = {name for _, name in ACTION_ATTR.findall(INDEX + APP)}
    assert named, "the controls name their actions with data-<event>"
    assert named <= _allowlisted_actions(), named - _allowlisted_actions()


def test_chartjs_is_loaded_from_the_url_the_policy_allows():
    assert f"const CHARTJS_SRC = '{CHARTJS_URL}';" in APP
