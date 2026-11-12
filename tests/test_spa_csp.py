"""The dashboard SPA runs under a CSP without inline code (T09).

``security_headers.CONTENT_SECURITY_POLICY`` allows scripts from the
dashboard's own files only, so an ``onclick="…"`` attribute or an inline
``<script>`` would simply not run. These checks keep the markup that way and
make sure every action a control names exists in the SPA's allowlist.
"""

import re
from pathlib import Path

from src.webui.security_headers import CHARTJS_URL

STATIC = Path(__file__).resolve().parents[1] / "src" / "webui" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")

INLINE_HANDLER = re.compile(r"""\son[a-z]+\s*=\s*["'`\\]""", re.IGNORECASE)
ACTION_ATTR = re.compile(r'data-(click|change|input|mousedown|keydown)="([\w-]+)"')


def _allowlisted_actions() -> set[str]:
    block = APP[APP.index("const ACTIONS = {") : APP.index("};", APP.index("const ACTIONS = {"))]
    block = re.sub(r"//[^\n]*", "", block)
    names = set(re.findall(r"'([\w-]+)'\s*:", block))
    names |= {n for n in re.findall(r"\b([A-Za-z]\w*)\b(?=\s*[,}\n])", block) if n != "ACTIONS"}
    return names


def test_no_inline_script_or_handler_in_the_page():
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", INDEX, re.IGNORECASE) == []
    assert '<script src="/static/app.js"></script>' in INDEX
    assert INLINE_HANDLER.findall(INDEX) == []


def test_no_inline_handler_in_the_markup_the_script_builds():
    assert INLINE_HANDLER.findall(APP) == []
    assert "javascript:" not in APP and "javascript:" not in INDEX
    assert "eval(" not in APP and "new Function" not in APP


def test_every_action_a_control_names_is_allowlisted():
    named = {name for _, name in ACTION_ATTR.findall(INDEX + APP)}
    assert named, "the controls name their actions with data-<event>"
    assert named <= _allowlisted_actions(), named - _allowlisted_actions()


def test_chartjs_is_loaded_from_the_url_the_policy_allows():
    assert f"const CHARTJS_SRC = '{CHARTJS_URL}';" in APP
