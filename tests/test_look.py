"""One look for Michel's embeds (P6): the palette, the cards, and the guard.

Colours, embeds and footers are made in ``src/discord_ext/look.py`` only: a
raw ``0x…`` colour, an ``interactions`` colour class, an ``Embed(...)`` or a
``set_footer`` anywhere else fails here. A reply's ✅ / ❌ comes from ``ok()`` /
``oops()``, never typed (P13).
"""

import ast
import re

import pytest
from source_scan import BOT_SOURCES, Imports, python_files, rel

from src.discord_ext import look
from src.discord_ext.look import (
    DARK_EMBED_BACKGROUND,
    LIGHT_EMBED_BACKGROUND,
    LOOKS,
    STATUS,
    Palette,
    ask,
    card,
    custom,
    footer_text,
    ok,
    oops,
    recolour,
    sign,
)

LOOK_FILE = "src/discord_ext/look.py"
COLOUR_CLASSES = {
    "interactions.MaterialColors",
    "interactions.BrandColors",
    "interactions.FlatUIColors",
    "interactions.RoleColors",
    "interactions.Color",
    "interactions.Colour",
}
EMBED = {
    "interactions.Embed",
    "interactions.ContainerComponent",
    "interactions.models.discord.components.ContainerComponent",
    "interactions.models.discord.embed.Embed",
    "interactions.EmbedFooter",
}
COLOUR_NAME = re.compile(r"colou?rs?($|_)|_colou?rs?", re.IGNORECASE)


def luminance(rgb: int) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (((rgb >> shift) & 255) / 255 for shift in (16, 8, 0))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: int, b: int) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


PALETTE = {name: value for name, value in vars(Palette).items() if name.isupper()}


@pytest.mark.parametrize("name", sorted(PALETTE))
def test_every_colour_reads_on_both_themes(name):
    """A colour stripe is a graphic: WCAG asks 3:1 against its background."""
    value = PALETTE[name]
    assert contrast(value, DARK_EMBED_BACKGROUND) >= 3, f"{name} on the dark theme"
    assert contrast(value, LIGHT_EMBED_BACKGROUND) >= 3, f"{name} on the light theme"


def test_every_look_and_status_uses_the_palette():
    used = {found.accent for found in LOOKS.values()} | set(STATUS.values())
    assert used <= set(PALETTE.values())


def test_a_card_carries_the_module_and_michel():
    embed = card("moduleXp", title="Classement", footer="Page 2/3")
    assert embed.color == LOOKS["moduleXp"].accent
    assert embed.author.name == "⭐ Système d'XP"
    assert embed.footer.text == "Michel · Système d'XP · Page 2/3"


def test_a_status_card_takes_the_status_colour():
    assert card("moduleXp", "error").color == Palette.RED
    assert card("moduleXp", "success").color == Palette.GREEN
    assert card("moduleXp", "neutral").color == Palette.GREY


def test_an_admin_accent_replaces_the_module_colour():
    assert card("moduleRss", accent=0x123456).color == 0x123456


def test_a_card_can_name_someone_instead_of_the_module():
    assert card("moduleSuggestions", author=False).author is None


def test_michel_signs_alone():
    assert footer_text("michel") == "Michel"
    assert footer_text("michel", "Page 1/4") == "Michel · Page 1/4"


def test_sign_and_recolour_rewrite_a_card():
    embed = card("moduleGiveaway")
    sign(embed, "moduleGiveaway", "3 gagnants")
    recolour(embed, "moduleGiveaway", "neutral")
    assert embed.footer.text == "Michel · Giveaways · 3 gagnants"
    assert embed.color == Palette.GREY


def test_an_admin_embed_has_no_branding():
    embed = custom(title="Règles", color=0x123456)
    assert embed.author is None and embed.footer is None and embed.color == 0x123456


def test_replies_start_with_what_happened():
    assert ok("Rappel créé.") == "✅ Rappel créé."
    assert oops("Date inconnue.", "📅") == "📅 Date inconnue."
    assert oops("Non.") == "❌ Non."
    assert ask("Tu confirmes ?") == "❓ Tu confirmes ?"


# ── The guard ────────────────────────────────────────────────────────


def look_offenders(source: str, path=None) -> list[int]:
    """Lines building a colour, an embed or a footer outside ``look.py``."""
    tree = ast.parse(source)
    imports = Imports(tree, path)
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute | ast.Name) and imports.resolve(node) in COLOUR_CLASSES:
            lines.append(node.lineno)
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            named = any(
                isinstance(t, ast.Name | ast.Attribute)
                and COLOUR_NAME.search(t.id if isinstance(t, ast.Name) else t.attr)
                for t in targets
            )
            if named and any(_is_rgb(n) for n in ast.walk(node.value)):
                lines.append(node.lineno)
        if isinstance(node, ast.Call):
            if imports.resolve(node.func) in EMBED:
                lines.append(node.lineno)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "set_footer":
                lines.append(node.lineno)
            for kw in node.keywords:
                if kw.arg in ("color", "colour", "accent") and _is_literal_int(kw.value):
                    lines.append(node.lineno)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value in ("color", "colour")
                    and _is_literal_int(value)
                ):
                    lines.append(node.lineno)
    return sorted(set(lines))


def _is_literal_int(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int


def _is_rgb(node: ast.AST) -> bool:
    """An integer literal that can only be a colour (beyond a byte)."""
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value > 0xFF


def test_colours_embeds_and_footers_come_from_look():
    offenders = [
        f"{rel(file)}:{line}"
        for file in python_files(*BOT_SOURCES)
        if rel(file) != LOOK_FILE
        for line in look_offenders(file.read_text(encoding="utf-8"), file)
    ]
    assert offenders == [], (
        "build embeds with card() (or custom() for one the admins designed), "
        "footers with card(footer=…) / sign(), colours from look.Palette"
    )


@pytest.mark.parametrize(
    "source",
    [
        "from interactions import Embed\nEmbed(title='x')",
        "import interactions\ninteractions.Embed()",
        "import interactions as ipy\nipy.Embed()",
        "from interactions import MaterialColors\nx = MaterialColors.RED",
        "import interactions\nx = interactions.BrandColors.GREEN",
        "embed.set_footer(text='Créé par x')",
        "card('moduleXp', color=0x00FF00)",
        "card('moduleRss', accent=0x00FF00)",
        "x = {'title': 't', 'color': 16711680}",
        "EMBED_COLOR_FRANCE = 0x002395",
        "color = 0xFF4500 if is_start else 0x8B0000",
        "MEDAL_COLORS = {'gold': 0xFFD700}",
        "embed.color = 0x00FF00",
        "from interactions import EmbedFooter\ncard('m', footer=EmbedFooter(text='Michel'))",
        "from interactions import ContainerComponent\nContainerComponent(accent_color=1)",
    ],
)
def test_the_guard_catches(source):
    assert look_offenders(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "from src.discord_ext.look import card\ncard('moduleXp', title='x')",
        "from interactions import Embed\nEmbed.from_dict({})",
        "custom(title='x', color=configured)",
        "colour_count = 3",
        "DEFAULT_COLOR = '3498db'",
    ],
)
def test_the_guard_lets_through(source):
    assert look_offenders(source) == []


# ── Replies: the icon comes from ok() / oops() ──────────────────────

REPLY_CALLS = {"send", "reply", "edit", "respond", "send_message"}
REPLY_ICONS = ("✅ ", "❌ ")


def _leading_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def reply_icon_offenders(source: str) -> list[int]:
    """Lines sending a reply that types its ✅ / ❌ instead of calling ok() / oops()."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in REPLY_CALLS
        ):
            continue
        texts = node.args[:1] + [k.value for k in node.keywords if k.arg == "content"]
        if any((_leading_text(t) or "").startswith(REPLY_ICONS) for t in texts):
            lines.append(node.lineno)
    return lines


def test_replies_take_their_icon_from_ok_and_oops():
    offenders = [
        f"{rel(file)}:{line}"
        for file in python_files(*BOT_SOURCES)
        for line in reply_icon_offenders(file.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        'start a reply with ok("…") (done) or oops("…") (refused), '
        "not a typed ✅ / ❌: see docs/VOIX.md"
    )


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        ("await ctx.send('❌ Non.', ephemeral=True)", True),
        ("await ctx.send(f'✅ {n} supprimés.')", True),
        ("await msg.edit(content='✅ Fait.')", True),
        ("await ctx.send(oops('Non.'), ephemeral=True)", False),
        ("Button(emoji='✅', label='Approuver')", False),
        ("STATUS = '✅ Approuvée'", False),
    ],
)
def test_the_reply_guard(source, caught):
    assert bool(reply_icon_offenders(source)) is caught


def test_the_module_exports_what_it_defines():
    assert set(look.__all__) <= set(vars(look))


# ── Components V2 cards (P22) ────────────────────────────────────────


def test_a_v2_card_has_the_module_stripe_its_name_and_michels_signature():
    from src.discord_ext.look import card_v2

    container = card_v2(
        "moduleAgenda",
        title="Les 14 prochains jours",
        blocks=["**Demain**\n📅 Soirée"],
        footer="toutes les 30 minutes",
    )
    assert container.accent_color == LOOKS["moduleAgenda"].accent
    texts = [part.content for part in container.components if hasattr(part, "content")]
    assert texts == [
        "-# 🗓️ Agenda\n## Les 14 prochains jours",
        "**Demain**\n📅 Soirée",
        "-# Michel · Agenda · toutes les 30 minutes",
    ]


def test_v2_blocks_are_packed_within_discords_limits():
    from src.discord_ext.look import V2_MAX_TEXTS, v2_texts

    assert v2_texts(["a", "b"]) == ["a\n\nb"]
    assert v2_texts(["x" * 3000, "y" * 3000]) == ["x" * 3000, "y" * 3000]
    assert len(v2_texts(["z" * 4000] * 50)) == V2_MAX_TEXTS
    assert v2_texts(["w" * 5000]) == ["w" * 4000]


def test_an_embed_becomes_a_v2_card_with_its_parts():
    from interactions.models.discord.components import process_components

    from src.core import clock
    from src.discord_ext.look import embeds_to_v2

    embed = card(
        "moduleZevent",
        title="Zevent",
        url="https://zevent.fr",
        description="En direct",
        timestamp=clock.now(),
    )
    embed.add_field(name="Total", value="150 000 €")
    embed.set_thumbnail("attachment://Zevent_logo.png")
    embed.set_image("https://example.com/banner.png")
    (container,) = process_components(embeds_to_v2([embed]))
    parts = container["components"]
    assert container["accent_color"] == LOOKS["moduleZevent"].accent
    assert (
        parts[0]["type"] == 9
        and parts[0]["accessory"]["media"]["url"] == "attachment://Zevent_logo.png"
    )
    assert parts[0]["components"][0]["content"] == "-# 🎉 Zevent\n## [Zevent](https://zevent.fr)"
    assert parts[2]["content"] == "En direct\n\n**Total**\n150 000 €"
    assert parts[3]["items"][0]["media"]["url"] == "https://example.com/banner.png"
    assert parts[-1]["content"] == "-# Michel · Zevent"  # no timestamp: a refresh would differ


def test_v2_fields_keep_their_own_bold_and_drop_spacers():
    from interactions.models.discord.components import process_components

    from src.discord_ext.look import SPACER_FIELD, embeds_to_v2

    embed = card("moduleVlrgg", title="Derniers matchs de Mandatory")
    embed.add_field(name="**Mandatory** **2**-1 Barça", value="Hav **13**-8", inline=True)
    embed.add_field(**SPACER_FIELD)
    embed.add_field(name="Mandatory 0-**2** **Eintracht**", value="Fra 5-**13**", inline=True)
    embed.add_field(name="​", value="Sans titre")
    embed.add_field(name="Seul", value="​")
    (container,) = process_components(embeds_to_v2([embed]))
    body = container["components"][2]["content"]
    assert body == (
        "**Mandatory** **2**-1 Barça\nHav **13**-8\n\n"
        "Mandatory 0-**2** **Eintracht**\nFra 5-**13**\n\n"
        "Sans titre\n\n"
        "**Seul**"
    )
    assert "​" not in body


def test_too_many_parts_fold_each_embed_into_one_text():
    from src.discord_ext.look import V2_MAX_COMPONENTS, _count, embeds_to_v2

    embeds = []
    for i in range(6):
        embed = card("moduleZevent", title=f"Lieu {i}", description="x")
        embed.set_thumbnail("https://example.com/t.png")
        embed.set_image("https://example.com/i.png")
        embeds.append(embed)
    cards = embeds_to_v2(embeds, extra=6)
    assert sum(_count(c) for c in cards) + 6 <= V2_MAX_COMPONENTS
    assert [len(c.components) for c in cards] == [1] * 6
    assert cards[0].components[0].content.startswith("-# 🎉 Zevent\n## Lieu 0")
