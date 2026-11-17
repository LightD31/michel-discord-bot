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
        self._guild_id = 1  # the starred message's guild
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


# ── Private sources (S06) ────────────────────────────────────────────────


class _VisibleTo:
    """A channel @everyone can (or can't) see."""

    def __init__(self, public, *, cid=20, nsfw=False):
        self.id = cid
        self.name = "salon"
        self.nsfw = nsfw
        self.public = public
        self.guild = SimpleNamespace(id=1, default_role="everyone")
        self.sent = []

    def permissions_for(self, role):
        from interactions import Permissions

        return Permissions.VIEW_CHANNEL if self.public else Permissions.NONE

    async def send(self, embeds):
        self.sent.append(embeds)
        return SimpleNamespace(id=60)


def _starred_in(channel):
    message = _starred(False)
    message.channel = channel
    return message


@pytest.mark.parametrize(
    ("source_public", "board_public", "allow", "mirrored"),
    [
        (False, True, False, False),  # staff channel → public starboard: held back
        (False, True, True, True),  # the guild allowed it
        (False, False, False, True),  # a staff starboard
        (True, True, False, True),
    ],
)
async def test_private_channels_stay_private(source_public, board_public, allow, mirrored):
    board = _VisibleTo(board_public, cid=50)
    ext, stored = _mixin(board)
    message = _starred_in(_VisibleTo(source_public))
    await ext._post_mirror(message, "1", 3, "⭐", "50", allow_private=allow)
    assert bool(board.sent) is mirrored and bool(stored) is mirrored


def test_a_thread_is_as_public_as_its_parent_unless_private():
    from src.discord_ext.channels import everyone_can_view

    public_parent = _VisibleTo(True)
    assert everyone_can_view(SimpleNamespace(parent_channel=public_parent, is_private=False))
    assert not everyone_can_view(SimpleNamespace(parent_channel=public_parent, is_private=True))
    assert not everyone_can_view(SimpleNamespace(parent_channel=_VisibleTo(False)))
    assert not everyone_can_view(SimpleNamespace(id=1))  # unknown: counts as private


def test_the_rule_itself():
    from features.starboard import exposes_hidden_channel

    assert exposes_hidden_channel(source_public=False, board_public=True, allow_private=False)
    assert not exposes_hidden_channel(source_public=False, board_public=True, allow_private=True)
    assert not exposes_hidden_channel(source_public=True, board_public=True, allow_private=False)
