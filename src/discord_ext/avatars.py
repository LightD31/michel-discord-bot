"""A member's avatar as PNG bytes, for the cards Pillow draws (rank, welcome)."""

from __future__ import annotations

from typing import Any

from src.core import logging as logutil

logger = logutil.init_logger(__name__)


async def avatar_png(user: Any, size: int = 256) -> bytes | None:
    """*user*'s avatar as a still PNG, or ``None`` (no avatar, or Discord's CDN refused).

    ``Asset.fetch()`` goes through the bot's HTTP client, which handles the CDN
    (``str(asset)`` is the attrs repr, not a URL); the PNG extension turns an
    animated avatar into one frame.
    """
    asset = getattr(user, "display_avatar", None) or getattr(user, "avatar", None)
    if asset is None:
        return None
    try:
        return await asset.fetch(extension=".png", size=size)
    except Exception as e:
        logger.warning("Avatar of %s not fetched: %s", getattr(user, "id", "?"), e)
        return None
