"""What a custom command answers: its text filled in, and the image it shows (A9).

A response is free text an admin wrote. ``{auteur}`` is the member who ran the
command, ``{cible}`` the member they aimed it at (``tout le monde`` when the
target is optional and they named nobody), ``{serveur}`` the server's name.
A link to an image (``.gif``, ``.png``…) is taken out of the text and shown
as the embed's image, so ``/calin`` reads « Hugo fait un câlin à Léa 🤗 »
over the GIF rather than above a bare link. Any other link stays in the text,
where Discord previews it itself (a Tenor page, a video).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from src.core.text import fill_message

VARIABLES = ("auteur", "cible", "serveur")
NOBODY_AIMED = "tout le monde"
IMAGE_EXTENSIONS = (".gif", ".png", ".jpg", ".jpeg", ".webp")
_SCHEMES = ("http://", "https://")


@dataclass(frozen=True)
class Response:
    text: str
    image_url: str | None = None


def is_image_url(token: str) -> bool:
    """True for a link Discord can show as an embed image (by its path's extension)."""
    if not token.lower().startswith(_SCHEMES):
        return False
    parts = urlsplit(token)
    return bool(parts.hostname) and parts.path.lower().endswith(IMAGE_EXTENSIONS)


def split_image(text: str) -> Response:
    """*text* without its last image link, and that link (``None`` without one)."""
    for token in reversed(text.split()):
        candidate = token.strip("<>")
        if is_image_url(candidate):
            at = text.rfind(token)
            return Response((text[:at] + text[at + len(token) :]).strip(), candidate)
    return Response(text.strip())


def fill(text: str, *, author: str, target: str | None, server: str) -> str:
    """Fill the three variables; an unknown ``{name}`` stays as written."""
    return fill_message(
        text, auteur=author, cible=target if target else NOBODY_AIMED, serveur=server
    )


def render_response(text: str, *, author: str, target: str | None, server: str) -> Response:
    """The response as the bot posts it: the filled text and its image."""
    split = split_image(text)
    filled = fill(split.text, author=author, target=target, server=server) if split.text else ""
    return Response(filled, split.image_url)


__all__ = [
    "IMAGE_EXTENSIONS",
    "NOBODY_AIMED",
    "VARIABLES",
    "Response",
    "fill",
    "is_image_url",
    "render_response",
    "split_image",
]
