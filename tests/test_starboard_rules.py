"""Starboard: which reactions count, and what happens to the mirror when they change."""

from types import SimpleNamespace

import pytest

from features.starboard import (
    MirrorAction,
    count_valid_reactors,
    emoji_matches,
    first_image_url,
    plan_mirror,
)


@pytest.mark.parametrize(
    ("reaction", "configured"),
    [
        ("⭐", "⭐"),
        ("⭐", "⭐️"),  # pasted with the emoji presentation selector
        ("❤️", "❤"),
        ("<:star2:123>", "star2"),
        ("<:star2:123>", ":star2:"),
        ("<:star2:123>", " :star2: "),
        ("<:star2:123>", "123"),
        ("<a:star2:123>", "star2"),  # animated
        ("<:star2:123>", "<:star2:123>"),
        ("<:renamed:123>", "<:star2:123>"),  # same emoji, renamed since
    ],
)
def test_the_configured_emoji_matches(reaction, configured):
    assert emoji_matches(reaction, configured)


@pytest.mark.parametrize(
    ("reaction", "configured"),
    [
        ("🌟", "⭐"),
        ("<:star2:123>", "⭐"),
        ("⭐", "star2"),
        ("<:star2:123>", "star"),
        ("<:star2:123>", "<:star2:456>"),
    ],
)
def test_other_emojis_do_not(reaction, configured):
    assert not emoji_matches(reaction, configured)


def users(*specs):
    return [SimpleNamespace(id=uid, bot=bot) for uid, bot in specs]


def test_bots_and_the_author_are_left_out_by_default():
    reactors = users((1, False), (2, False), (3, True), (99, False))  # 99 is the author
    assert count_valid_reactors(reactors, author_id=99, allow_self=False, ignore_bots=True) == 2
    assert count_valid_reactors(reactors, author_id=99, allow_self=True, ignore_bots=True) == 3
    assert count_valid_reactors(reactors, author_id=99, allow_self=True, ignore_bots=False) == 4


@pytest.mark.parametrize(
    ("stored", "count", "remove_below", "action"),
    [
        (None, 2, False, MirrorAction.NOTHING),  # not there yet, below threshold
        (None, 3, False, MirrorAction.POST),
        (None, 7, True, MirrorAction.POST),
        (3, 3, False, MirrorAction.NOTHING),  # unchanged
        (3, 4, False, MirrorAction.REFRESH),
        (4, 3, True, MirrorAction.REFRESH),  # still at the threshold
        (3, 2, True, MirrorAction.REMOVE),
        (3, 2, False, MirrorAction.REFRESH),  # kept, count updated
        (2, 2, False, MirrorAction.NOTHING),
        (3, 0, True, MirrorAction.REMOVE),
    ],
)
def test_mirror_plan(stored, count, remove_below, action):
    assert plan_mirror(stored, count, 3, remove_below=remove_below) is action


def attachment(url, content_type=None):
    return SimpleNamespace(url=url, content_type=content_type)


def test_first_image_prefers_attachments_then_embeds():
    message = SimpleNamespace(
        attachments=[
            attachment("https://cdn/notes.txt", "text/plain"),
            attachment("https://cdn/a.png", "image/png"),
        ],
        embeds=[SimpleNamespace(image=SimpleNamespace(url="https://cdn/embed.png"))],
    )
    assert first_image_url(message) == "https://cdn/a.png"


def test_an_attachment_without_content_type_is_recognised_by_extension():
    message = SimpleNamespace(attachments=[attachment("https://cdn/photo.JPG?ex=1")], embeds=[])
    assert first_image_url(message) == "https://cdn/photo.JPG?ex=1"


def test_embed_image_then_thumbnail():
    thumb_only = SimpleNamespace(image=None, thumbnail=SimpleNamespace(url="https://cdn/t.png"))
    assert first_image_url(SimpleNamespace(attachments=[], embeds=[thumb_only])) == (
        "https://cdn/t.png"
    )
    assert first_image_url(SimpleNamespace(attachments=None, embeds=None)) is None


# ── NSFW sources ────────────────────────────────────────────────────────


class _Channel:
    def __init__(self, nsfw):
        self.id = 50
        self.nsfw = nsfw
        self.sent = []

    async def send(self, embeds):
        self.sent.append(embeds)
        return SimpleNamespace(id=60)


def _starred(source_nsfw):
    author = SimpleNamespace(id=1, display_name="alice", avatar_url=None, bot=False)
    return SimpleNamespace(
        id=10,
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=20, name="memes", nsfw=source_nsfw),
        author=author,
        content="regardez",
        attachments=[],
        embeds=[],
        created_at=None,
    )


def _mixin(starboard):
    from extensions.starboard.listeners import ListenersMixin

    stored = []

    class Ext(ListenersMixin):
        pass

    async def fetch_channel(cid):
        return starboard

    async def upsert(entry):
        stored.append(entry)

    ext = Ext()
    ext.bot = SimpleNamespace(fetch_channel=fetch_channel)
    ext.repository = lambda guild_id: SimpleNamespace(upsert=upsert)
    return ext, stored


@pytest.mark.parametrize(
    ("source_nsfw", "board_nsfw", "mirrored"),
    [(True, False, False), (True, True, True), (False, False, True)],
)
async def test_nsfw_messages_reach_only_an_nsfw_starboard(source_nsfw, board_nsfw, mirrored):
    board = _Channel(board_nsfw)
    ext, stored = _mixin(board)
    await ext._post_mirror(_starred(source_nsfw), "1", 3, "⭐", "50")
    assert bool(board.sent) is mirrored and bool(stored) is mirrored
