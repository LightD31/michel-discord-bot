"""Render a welcome/leave card image with the member's avatar and name."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_FONT_PATH = str(Path(__file__).resolve().parent.parent.parent / "src" / "assets" / "OpenSans.ttf")

CARD_WIDTH = 800
CARD_HEIGHT = 250
AVATAR_SIZE = 160
PADDING = 32

_BG_COLOR = (30, 31, 34)
_ACCENT_COLOR = (90, 110, 230)
_TEXT_PRIMARY = (242, 243, 245)
_TEXT_SECONDARY = (170, 175, 185)


def _circular_avatar(avatar_bytes: bytes, size: int) -> Image.Image:
    """Crop an avatar to a circle of *size* pixels."""
    img = Image.open(BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask=mask)
    return out


def render_welcome_card(
    *,
    avatar_bytes: bytes | None,
    username: str,
    title: str = "Bienvenue",
    subtitle: str | None = None,
) -> BytesIO:
    """Render a 800×250 welcome card. Returns a PNG ``BytesIO`` ready to upload.

    ``avatar_bytes`` should be the raw bytes of the user's avatar (PNG/JPEG/GIF
    first frame). When None, the avatar circle is left empty.
    """
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _BG_COLOR)
    draw = ImageDraw.Draw(card)

    # Soft accent glow behind where the avatar will sit.
    glow_size = AVATAR_SIZE + 60
    glow_x = PADDING + (AVATAR_SIZE // 2) - (glow_size // 2)
    glow_y = (CARD_HEIGHT // 2) - (glow_size // 2)
    glow = Image.new("RGBA", (glow_size, glow_size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (0, 0, glow_size, glow_size), fill=(*_ACCENT_COLOR, 90)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(20))
    card.paste(glow, (glow_x, glow_y), glow)

    # Avatar circle (or placeholder).
    avatar_x = PADDING
    avatar_y = (CARD_HEIGHT - AVATAR_SIZE) // 2
    if avatar_bytes:
        try:
            avatar = _circular_avatar(avatar_bytes, AVATAR_SIZE)
            card.paste(avatar, (avatar_x, avatar_y), avatar)
        except Exception:
            draw.ellipse(
                (avatar_x, avatar_y, avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE),
                fill=_ACCENT_COLOR,
            )
    else:
        draw.ellipse(
            (avatar_x, avatar_y, avatar_x + AVATAR_SIZE, avatar_y + AVATAR_SIZE),
            fill=_ACCENT_COLOR,
        )

    # Text block right of the avatar.
    text_x = PADDING + AVATAR_SIZE + PADDING
    title_font = ImageFont.truetype(_FONT_PATH, 36)
    name_font = ImageFont.truetype(_FONT_PATH, 44)
    sub_font = ImageFont.truetype(_FONT_PATH, 22)

    draw.text((text_x, PADDING + 10), title, font=title_font, fill=_ACCENT_COLOR)

    # Truncate over-long usernames so the layout stays readable.
    display = username if len(username) <= 22 else username[:21] + "…"
    draw.text((text_x, PADDING + 60), display, font=name_font, fill=_TEXT_PRIMARY)

    if subtitle:
        draw.text((text_x, PADDING + 130), subtitle, font=sub_font, fill=_TEXT_SECONDARY)

    buffer = BytesIO()
    card.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


__all__ = ["render_welcome_card"]
