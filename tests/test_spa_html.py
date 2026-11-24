"""html`…` escapes what it interpolates, in a real browser.

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

dashboard = smoke.dashboard
page = smoke.page

# escapeHtml( calls left in js/, still to move to html`…`. Lower it as pages
# move over; never raise it (a new page uses html`…`).
ESCAPE_CALLS_CEILING = 6  # P17: the helpers' own definitions, mdInline and the log search marks
# Pages written with html`…` only.
HTML_ONLY = [
    "features/gazette.js",
    "features/moderation.js",
    "features/rolemenus.js",
    "features/secretsanta.js",
    "features/spotify-auth.js",
    "features/spotify.js",
    "features/xp.js",
    "forms/checks.js",
    "forms/fields.js",
    "forms/pickers/channels.js",
    "forms/pickers/lists.js",
    "forms/pickers/members.js",
    "forms/pickers/roles.js",
    "forms/save-bar.js",
    "forms/save.js",
    "forms/template-test.js",
    "forms/widgets/accounts.js",
    "forms/widgets/commands.js",
    "forms/widgets/embeds.js",
    "forms/widgets/maps.js",
    "forms/widgets/messages.js",
    "forms/widgets/models.js",
    "forms/widgets/rss.js",
    "forms/widgets/spotify-users.js",
    "forms/widgets/streamers.js",
    "forms/widgets/teams.js",
    "forms/widgets/youtube.js",
    "pages/audit.js",
    "pages/bot-messages.js",
    "pages/extensions.js",
    "pages/global.js",
    "pages/health.js",
    "pages/home.js",
    "pages/links.js",
    "pages/me.js",
    "pages/module.js",
    "pages/onboarding.js",
    "pages/overview.js",
    "pages/public.js",
    "pages/server.js",
    "pages/setup.js",
    "ui/charts.js",
    "ui/confirm.js",
    "ui/discord.js",
    "ui/modal.js",
    "ui/page-index.js",
    "ui/sidebar.js",
    "ui/toast.js",
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


def test_inline_styles_only_carry_computed_values():
    # Layout is in app.css (u-* utilities or a named class); a style="…" holds
    # a value worked out at render time (a width in %, a role colour), and
    # showing or hiding is the hidden attribute, not style.display.
    for path, code in MODULES.items():
        for attr in re.findall(r'style="([^"]*)"', code):
            assert "${" in attr, f'{path}: static style="{attr}", use a class'
        assert ".style.display" not in code, f"{path}: toggle el.hidden instead"


RENDER = """async (texts) => {
    const v = new URL(document.querySelector('script[type=module]').src).search;
    const { discordMarkdown } = await import(`/static/js/ui/discord.js${v}`);
    const names = { users: new Map([['7', 'Alice <3']]), channels: new Map([['9', 'général']]),
                    roles: new Map() };
    return texts.map(t => String(discordMarkdown(t, names)));
}"""


def test_the_discord_preview_draws_markdown_and_never_markup(page):
    """What an admin writes is shown as Discord would, and stays text."""
    out = page.evaluate(
        RENDER,
        [
            "**gras** *italique* __souligné__ ~~barré~~ ||spoiler||",
            "<script>alert(1)</script> <img src=x onerror=go()>",
            "Salut <@7> dans <#9>, <@&5> et <@8>",
            "[clic](javascript:alert(1)) [site](https://ex.com/a_b**c**)",
            "https://ex.com/<@7>",
            "`**pas gras** <b>` et ```\n<i>bloc</i>\n```",
            "> citation\n# Titre\n-# petit",
            "@everyone 0",
        ],
    )
    assert out[0] == (
        '<div class="dc-line"><strong>gras</strong> <em>italique</em> <u>souligné</u> '
        '<s>barré</s> <span class="dc-spoiler">spoiler</span></div>'
    )
    assert "<script" not in out[1] and "<img" not in out[1]
    assert "&lt;script&gt;" in out[1] and "&lt;img src=x onerror=go()&gt;" in out[1]
    assert '<span class="dc-mention">@Alice &lt;3</span>' in out[2]
    assert '<span class="dc-mention">#général</span>' in out[2]
    assert "@rôle inconnu" in out[2] and "@utilisateur inconnu" in out[2]
    assert "javascript:" not in out[3].split("<a ", 1)[-1].split(">")[0]  # never a link
    assert 'href="https://ex.com/a_b**c**"' in out[3]  # a URL is not formatted
    # No markup ever lands in an href: the mention is cut out before the URL.
    for anchor in out[4].split("<a ")[1:]:
        assert "<" not in anchor.split(">", 1)[0]
    assert "<code>**pas gras** &lt;b&gt;</code>" in out[5]
    assert "<code>&lt;i&gt;bloc&lt;/i&gt;</code>" in out[5]
    assert out[6].startswith("<blockquote>citation</blockquote>")
    assert (
        '<div class="dc-h1">Titre</div>' in out[6] and '<div class="dc-sub">petit</div>' in out[6]
    )
    assert '<span class="dc-mention">@everyone</span>' in out[7]
    assert "" not in out[7]  # the slot mark can't be typed in
