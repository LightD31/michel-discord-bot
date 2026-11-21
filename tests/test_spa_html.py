"""html`…` escapes what it interpolates, in a real browser (C8).

The SPA builds its markup with the ``html`` tag of ``js/core/html.js``: a
value is escaped unless it is itself ``html`…``` or ``raw()``. The pages moved
to it no longer call ``escapeHtml()`` one value at a time, and the number of
calls left in the SPA can only go down.
"""

import re

import pytest
from spa_sources import MODULES

pytest.importorskip("playwright.sync_api")

import test_spa_smoke as smoke  # noqa: E402

browser = smoke.browser
dashboard = smoke.dashboard
page = smoke.page

# escapeHtml( calls left in js/, still to move to html`…`. Lower it as pages
# move over; never raise it (a new page uses html`…`).
ESCAPE_CALLS_CEILING = 255
# Pages written with html`…` only.
HTML_ONLY = [
    "pages/audit.js",
    "pages/health.js",
    "pages/onboarding.js",
    "pages/overview.js",
    "pages/server.js",
    "pages/setup.js",
]

# The modules as the page loaded them (same ?v=), so they share one SafeHtml.
CHECKS = """async () => {
    const v = new URL(document.querySelector('script[type=module]').src).search;
    const { html, raw } = await import(`/static/js/core/html.js${v}`);
    const { actionArgs } = await import(`/static/js/core/actions.js${v}`);
    const evil = '<img src=x onerror="go()">';
    const rows = ['<b>', 'Ines & co'].map(name => html`<li title="${name}">${name}</li>`);
    return {
        text: String(html`<p>${evil}</p>`),
        attr: String(html`<a title="${'" onmouseover="x'}">`),
        quote: String(html`<a title='${"' x='1"}'>`),
        nested: String(html`<ul>${rows}</ul>`),
        raw: String(html`<div>${raw('<br>')}</div>`),
        empty: String(html`${null}${undefined}${false}${0}${''}`),
        args: String(html`<b data-click-args="${actionArgs('a"b')}">`),
        mixed: `<div>${html`<i>${'<x>'}</i>`}</div>`,
    };
}"""


def test_html_escapes_every_value_unless_it_is_html(page):
    out = page.evaluate(CHECKS)
    assert out["text"] == "<p>&lt;img src=x onerror=&quot;go()&quot;&gt;</p>"
    assert out["attr"] == '<a title="&quot; onmouseover=&quot;x">'
    assert out["quote"] == "<a title='&#39; x=&#39;1'>"
    assert out["nested"] == (
        '<ul><li title="&lt;b&gt;">&lt;b&gt;</li><li title="Ines &amp; co">Ines &amp; co</li></ul>'
    )
    assert out["raw"] == "<div><br></div>"
    assert out["empty"] == "0"  # nothing for null, undefined and false; 0 is a value
    # actionArgs() is escaped once, not twice, inside html`…`.
    assert out["args"] == '<b data-click-args="[&quot;a\\&quot;b&quot;]">'
    assert out["mixed"] == "<div><i>&lt;x&gt;</i></div>"  # reads as text in older code


def test_the_pages_moved_to_html_escape_nothing_by_hand():
    for path in HTML_ONLY:
        assert "escapeHtml" not in MODULES[path], path


def test_hand_escaping_only_goes_down():
    calls = sum(len(re.findall(r"\bescapeHtml\(", code)) for code in MODULES.values())
    assert calls <= ESCAPE_CALLS_CEILING, (
        f"{calls} escapeHtml( calls: build the new markup with html`…` (js/core/html.js)"
    )
