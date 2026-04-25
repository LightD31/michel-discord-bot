"""Pillow-based bar chart renderer for tricount expense breakdowns."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONT_PATH = str(Path(__file__).resolve().parent.parent.parent / "src" / "assets" / "OpenSans.ttf")

_BG = (24, 25, 28)
_PANEL = (35, 37, 41)
_GRID = (60, 62, 68)
_TEXT_PRIMARY = (242, 243, 245)
_TEXT_DIM = (170, 175, 185)

_BAR_PALETTE = [
    (88, 196, 132),
    (90, 110, 230),
    (230, 110, 90),
    (200, 160, 80),
    (180, 90, 200),
    (90, 200, 200),
    (230, 90, 150),
    (140, 230, 90),
]


def render_category_chart(
    *,
    title: str,
    category_totals: dict[str, float],
    currency: str = "€",
) -> BytesIO:
    """Render a horizontal bar chart of spending per category. PNG ``BytesIO``.

    Empty input is rendered as a single "No data" placeholder.
    """
    items = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    width = 900
    row_height = 44
    header_height = 90
    footer_height = 30
    height = max(180, header_height + footer_height + max(1, len(items)) * row_height + 20)

    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((10, 10, width - 10, height - 10), radius=18, fill=_PANEL)

    title_font = ImageFont.truetype(_FONT_PATH, 28)
    label_font = ImageFont.truetype(_FONT_PATH, 20)
    value_font = ImageFont.truetype(_FONT_PATH, 18)

    draw.text((30, 28), title, font=title_font, fill=_TEXT_PRIMARY)
    total = sum(v for _, v in items)
    draw.text(
        (30, 62),
        f"Total : {total:.2f}{currency}" if total else "Aucune donnée",
        font=value_font,
        fill=_TEXT_DIM,
    )

    if not items:
        return _to_buffer(img)

    label_w = 200
    bar_x = 30 + label_w + 10
    bar_max_w = width - bar_x - 30
    max_value = max(v for _, v in items) or 1.0

    for i, (label, value) in enumerate(items):
        y = header_height + i * row_height
        # Truncate long category labels to keep alignment.
        text_label = label if len(label) <= 22 else label[:21] + "…"
        draw.text((30, y + 6), text_label, font=label_font, fill=_TEXT_PRIMARY)

        # Background bar (full width, faded).
        draw.rounded_rectangle(
            (bar_x, y + 8, bar_x + bar_max_w, y + 32), radius=12, fill=_GRID
        )
        # Filled portion.
        bar_w = max(int(bar_max_w * (value / max_value)), 6)
        color = _BAR_PALETTE[i % len(_BAR_PALETTE)]
        draw.rounded_rectangle(
            (bar_x, y + 8, bar_x + bar_w, y + 32), radius=12, fill=color
        )
        pct = (value / total * 100) if total else 0
        value_text = f"{value:.2f}{currency} ({pct:.0f}%)"
        draw.text(
            (bar_x + bar_w + 8, y + 9),
            value_text,
            font=value_font,
            fill=_TEXT_DIM,
        )

    return _to_buffer(img)


def _to_buffer(img: Image.Image) -> BytesIO:
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


__all__ = ["render_category_chart"]
