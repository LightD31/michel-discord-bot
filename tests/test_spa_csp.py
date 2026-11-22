"""The dashboard SPA runs under a CSP without inline code (T09).

``security_headers.CONTENT_SECURITY_POLICY`` allows scripts from the
dashboard's own files only, so an ``onclick="…"`` attribute or an inline
``<script>`` would simply not run. These checks keep the markup that way and
make sure every action a control names exists in the SPA's allowlist, which
each module fills with ``registerActions({…})`` (C8).
"""

import re

import pytest
from spa_sources import INDEX, MODULES, SCRIPT

from src.webui.routes.frontend import IMPORT
from src.webui.security_headers import CHARTJS_URL

APP = SCRIPT

# An on…= attribute, quoted or not, in markup or built by the script.
INLINE_HANDLER = re.compile(r"""[\s"'/]on[a-z]+\s*=\s*["'`\\\w(]""", re.IGNORECASE)
SET_HANDLER = re.compile(r"""setAttribute\(\s*["'`]on""", re.IGNORECASE)
# A <script> without a src attribute (data-src="…" isn't one).
INLINE_SCRIPT = re.compile(r"<script(?![^>]*(?<![\w-])src\s*=)[^>]*>", re.IGNORECASE)
# Code from a string: eval, Function (with or without new), a string timer.
STRING_CODE = re.compile(r"""\beval\s*\(|\bFunction\s*\(|\bset(Timeout|Interval)\s*\(\s*["'`]""")
ACTION_ATTR = re.compile(r'data-(click|change|input|mousedown|keydown)="([\w-]+)"')


def _object_keys(code: str) -> set[str]:
    """The top-level keys of the object literal *code* starts in: not parameters."""
    names, depth, entry = set(), 0, ""
    code = re.sub(r"//[^\n]*", "", code)
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


def _allowlisted_actions(app: str = APP) -> set[str]:
    """The actions in ``const ACTIONS = {…}`` and in every ``registerActions({…})``."""
    names: set[str] = set()
    for opener in ("const ACTIONS = {", "registerActions({"):
        for match in re.finditer(re.escape(opener), app):
            names |= _object_keys(app[match.end() :])
    return names


NO_HANDLER = "the CSP refuses inline handlers: use data-action and registerActions"


def test_no_inline_script_or_handler_in_the_page():
    assert INLINE_SCRIPT.findall(INDEX) == [], "the CSP refuses inline scripts: move it to a module"
    assert '<script type="module" src="/static/js/main.js"></script>' in INDEX
    assert INLINE_HANDLER.findall(INDEX) == [], NO_HANDLER


def test_no_inline_handler_in_the_markup_the_script_builds():
    assert INLINE_HANDLER.findall(APP) == [], NO_HANDLER
    assert SET_HANDLER.findall(APP) == [], NO_HANDLER
    for text in (APP, INDEX):
        assert "javascript:" not in text.lower()
    assert STRING_CODE.findall(APP) == [], (
        'no code in strings (setTimeout("…"), new Function): the CSP refuses it'
    )


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
        (INLINE_SCRIPT, '<script type="module" src="/static/js/main.js"></script>'),
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
        "};\n"
        "registerActions({\n    loadPage,\n    'go-on': (event) => go(event),\n});"
    )
    assert _allowlisted_actions(app) == {"saveForm", "remove-closest", "other", "loadPage", "go-on"}
    assert "selector" not in _allowlisted_actions()


def test_every_action_a_control_names_is_allowlisted():
    named = {name for _, name in ACTION_ATTR.findall(INDEX + APP)}
    assert named, "the controls name their actions with data-<event>"
    assert named <= _allowlisted_actions(), named - _allowlisted_actions()


def test_chartjs_is_loaded_from_the_url_the_policy_allows():
    assert f"const CHARTJS_SRC = '{CHARTJS_URL}';" in MODULES["ui/charts.js"]


def test_every_module_is_loaded_by_the_entry_point():
    """main.js imports each module: its actions are registered, its listeners set."""
    loaded = set(re.findall(r"^import '\./([\w./-]+)';$", MODULES["main.js"], re.MULTILINE))
    assert loaded == set(MODULES) - {"main.js"}


def test_every_import_is_one_the_server_versions():
    """A static, relative, one-line import: the server adds ``?v=`` to it (frontend.py).

    Any other form (a bare or absolute specifier, ``import()``, an import split
    over lines) would load the file unversioned, from a cache a deploy doesn't
    clear.
    """
    for path, code in MODULES.items():
        imports = [
            line for line in code.splitlines() if re.match(r"\s*(import|export .* from)\b", line)
        ]
        assert len(IMPORT.findall(code)) == len(imports), path
        assert not re.search(r"\bimport\s*\(", code), path
