"""Image helpers — Pillow-based, no Discord dependency."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_DEFAULT_FONT_PATH = str(_ASSETS / "Menlo-Regular.ttf")
# The font of the rank card, the welcome card and the Tricount chart.
SANS_FONT_PATH = str(_ASSETS / "OpenSans.ttf")


def circular_avatar(avatar_bytes: bytes, size: int) -> Image.Image:
    """An avatar cropped to a circle of *size* pixels (the rank and welcome cards)."""
    img = Image.open(BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask=mask)
    return out


def create_dynamic_image(
    text: str,
    font_size: int = 20,
    font_path: str = _DEFAULT_FONT_PATH,
    image_padding: int = 10,
    background_color: str = "#1E1F22",
) -> tuple[Image.Image, BytesIO]:
    """Render *text* centered on a dark-themed rectangle.

    Returns ``(pillow_image, bytes_io)``; the caller can feed ``bytes_io`` into
    ``interactions.File`` or write it to disk.
    """
    if not text:
        raise ValueError("Text cannot be empty")
    if font_size <= 0:
        raise ValueError("Font size must be greater than zero")
    if image_padding < 0:
        raise ValueError("Image padding cannot be negative")

    font = ImageFont.truetype(font_path, font_size)
    draw = ImageDraw.Draw(Image.new("RGB", (0, 0)))
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]

    image_width = int(text_width + 2 * image_padding)
    image_height = int(text_height + 2 * image_padding)
    image = Image.new("RGB", (image_width, image_height), color=background_color)
    draw = ImageDraw.Draw(image)

    x = image_width // 2
    y = image_height // 2
    draw.text((x, y), text, font=font, fill=0xF2F3F5, anchor="mm")

    image_io = BytesIO()
    image.save(image_io, "png")
    image_io.seek(0)

    return image, image_io


__all__ = ["create_dynamic_image"]
