"""One look for Michel's embeds (P6, B8): the palette, the cards, and the guard.

Colours, embeds and footers are made in ``src/discord_ext/look.py`` only: a
raw ``0x…`` colour, an ``interactions`` colour class, an ``Embed(...)`` or a
``set_footer`` anywhere else fails here.
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


def test_the_module_exports_what_it_defines():
    assert set(look.__all__) <= set(vars(look))
