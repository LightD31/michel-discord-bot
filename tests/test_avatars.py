"""A member's avatar for the cards Pillow draws (P33, src/discord_ext/avatars.py)."""

from types import SimpleNamespace

from src.discord_ext.avatars import avatar_png


class Asset:
    def __init__(self, fail=False):
        self.fail = fail
        self.asked = None

    async def fetch(self, extension, size):
        self.asked = (extension, size)
        if self.fail:
            raise RuntimeError("CDN said no")
        return b"png"


async def test_the_display_avatar_is_fetched_as_a_still_png():
    asset = Asset()
    assert await avatar_png(SimpleNamespace(display_avatar=asset, id=1)) == b"png"
    assert asset.asked == (".png", 256)


async def test_no_avatar_or_a_refusing_cdn_is_none():
    assert await avatar_png(SimpleNamespace(id=1)) is None
    assert await avatar_png(SimpleNamespace(display_avatar=Asset(fail=True), id=1)) is None
