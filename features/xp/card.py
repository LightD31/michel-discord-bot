"""Image rank card renderer for the /rank command."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_FONT_PATH = str(Path(__file__).resolve().parent.parent.parent / "src" / "assets" / "OpenSans.ttf")

CARD_WIDTH = 900
CARD_HEIGHT = 280
AVATAR_SIZE = 180
PADDING = 30

_BG = (24, 25, 28)
_PANEL = (35, 37, 41)
_BAR_BG = (50, 52, 58)
_BAR_FG = (88, 196, 132)
_TEXT_PRIMARY = (242, 243, 245)
_TEXT_DIM = (170, 175, 185)
_ACCENT = (88, 196, 132)


def _circular_avatar(avatar_bytes: bytes, size: int) -> Image.Image:
    img = Image.open(BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask=mask)
    return out


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def render_rank_card(
    *,
    avatar_bytes: bytes | None,
    username: str,
    display_name: str,
    level: int,
    xp_in_level: int,
    xp_to_next: int,
    total_xp: int,
    rank: int | None,
    member_count: int | None,
    message_count: int,
) -> BytesIO:
    """Render a 900×280 rank card. Returns a PNG ``BytesIO`` ready to upload."""
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BG)
    draw = ImageDraw.Draw(card)

    # Inner panel for content.
    _rounded_rect(
        draw,
        (PADDING // 2, PADDING // 2, CARD_WIDTH - PADDING // 2, CARD_HEIGHT - PADDING // 2),
        radius=22,
        fill=_PANEL,
    )

    # Avatar with subtle accent glow.
    avatar_x, avatar_y = PADDING, (CARD_HEIGHT - AVATAR_SIZE) // 2
    glow_size = AVATAR_SIZE + 24
    glow = Image.new("RGBA", (glow_size, glow_size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((0, 0, glow_size, glow_size), fill=(*_ACCENT, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(12))
    card.paste(
        glow,
        (avatar_x - 12, avatar_y - 12),
        glow,
    )
    if avatar_bytes:
        try:
            avatar = _circular_avatar(avatar_bytes, AVATAR_SIZE)
            card.paste(avatar, (avatar_x, avatar_y), avatar)
        except Exception:
            draw.ellipse(
                (avatar_x, avatar_y, avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE),
                fill=_ACCENT,
            )
    else:
        draw.ellipse(
            (avatar_x, avatar_y, avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE),
            fill=_ACCENT,
        )

    text_x = avatar_x + AVATAR_SIZE + PADDING
    name_font = ImageFont.truetype(_FONT_PATH, 36)
    sub_font = ImageFont.truetype(_FONT_PATH, 20)
    big_font = ImageFont.truetype(_FONT_PATH, 30)
    small_font = ImageFont.truetype(_FONT_PATH, 18)

    # Top row: display name + (username) + rank chip.
    name_truncated = display_name if len(display_name) <= 18 else display_name[:17] + "…"
    draw.text((text_x, 40), name_truncated, font=name_font, fill=_TEXT_PRIMARY)
    if username and username != display_name:
        username_text = f"@{username}"
        if len(username_text) > 20:
            username_text = username_text[:19] + "…"
        draw.text((text_x, 86), username_text, font=sub_font, fill=_TEXT_DIM)

    rank_text = f"#{rank}" + (f" / {member_count}" if member_count else "") if rank else "—"
    rank_font = ImageFont.truetype(_FONT_PATH, 26)
    rank_bbox = draw.textbbox((0, 0), rank_text, font=rank_font)
    rank_w = rank_bbox[2] - rank_bbox[0]
    chip_w = rank_w + 28
    chip_h = 38
    chip_x = CARD_WIDTH - PADDING - chip_w
    chip_y = PADDING + 6
    _rounded_rect(draw, (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), 18, _ACCENT)
    draw.text(
        (chip_x + 14, chip_y + 6),
        rank_text,
        font=rank_font,
        fill=(20, 22, 25),
    )

    # Stats line.
    stats_text = f"Niveau {level} · {message_count} message(s) · XP total : {total_xp}"
    draw.text((text_x, 122), stats_text, font=big_font, fill=_TEXT_PRIMARY)

    # Progress bar.
    bar_x = text_x
    bar_y = 180
    bar_w = CARD_WIDTH - bar_x - PADDING
    bar_h = 26
    _rounded_rect(draw, (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), bar_h // 2, _BAR_BG)
    if xp_to_next > 0:
        ratio = max(0.0, min(1.0, xp_in_level / xp_to_next))
        if ratio > 0:
            filled_w = max(int(bar_w * ratio), bar_h)
            _rounded_rect(
                draw,
                (bar_x, bar_y, bar_x + filled_w, bar_y + bar_h),
                bar_h // 2,
                _BAR_FG,
            )
    progress_text = f"{xp_in_level} / {xp_to_next} XP"
    draw.text((bar_x, bar_y + bar_h + 6), progress_text, font=small_font, fill=_TEXT_DIM)

    buffer = BytesIO()
    card.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


__all__ = ["render_rank_card"]
