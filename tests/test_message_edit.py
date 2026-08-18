"""Tests for the change-aware message edit helper.

The contract under test is the ordering: diff first, unarchive only when
something changed, then edit. A periodic refresher must not keep resurrecting
an archived thread just to write the same bytes back.
"""

import pytest
from interactions import Embed

from src.discord_ext import messages as msgmod
from src.discord_ext.messages import edit_message_if_changed


class FakeThread:
    """Stands in for ``interactions.ThreadChannel`` (patched in per test)."""

    def __init__(self, archived: bool = False) -> None:
        self.id = 4242
        self.archived = archived
        self.edit_calls: list[dict] = []

    async def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        if "archived" in kwargs:
            self.archived = kwargs["archived"]


class FakeMessage:
    def __init__(self, *, content="", embeds=None, components=None, channel=None) -> None:
        self.id = 1234
        self.content = content
        self.embeds = embeds or []
        self.components = components or []
        self.channel = channel
        self.edit_calls: list[dict] = []

    async def edit(self, **kwargs):
        self.edit_calls.append(kwargs)


@pytest.fixture(autouse=True)
def _fake_thread_type(monkeypatch):
    """Make ``unarchive_if_thread`` recognise :class:`FakeThread`."""
    monkeypatch.setattr(msgmod, "ThreadChannel", FakeThread)


def _rendered(embed: Embed) -> Embed:
    """Round-trip an embed the way Discord echoes it back on the message."""
    return Embed.from_dict(embed.to_dict())


# ── Step 1: nothing changed ─────────────────────────────────────────────


async def test_identical_payload_is_a_noop():
    embed = Embed(title="Stats", description="42 joueurs")
    message = FakeMessage(content="", embeds=[_rendered(embed)])

    assert await edit_message_if_changed(message, content="", embeds=[embed]) is False
    assert message.edit_calls == []


async def test_noop_does_not_unarchive_the_thread():
    """The whole point of diffing first: no edit, no thread side effect."""
    thread = FakeThread(archived=True)
    embed = Embed(title="Stats", description="42 joueurs")
    message = FakeMessage(embeds=[_rendered(embed)], channel=thread)

    assert await edit_message_if_changed(message, embeds=[embed]) is False
    assert thread.edit_calls == []
    assert thread.archived is True


async def test_changed_content_triggers_an_edit():
    message = FakeMessage(content="ancien")

    assert await edit_message_if_changed(message, content="nouveau") is True
    assert message.edit_calls == [{"content": "nouveau"}]


async def test_changed_embed_triggers_an_edit():
    message = FakeMessage(embeds=[_rendered(Embed(title="a"))])

    assert await edit_message_if_changed(message, embeds=[Embed(title="b")]) is True
    assert len(message.edit_calls) == 1


async def test_changed_components_trigger_an_edit():
    from interactions import ActionRow, Button, ButtonStyle

    row = ActionRow(Button(style=ButtonStyle.PRIMARY, label="Voter", custom_id="v"))
    message = FakeMessage(components=[row])

    assert await edit_message_if_changed(message, components=[row]) is False
    assert await edit_message_if_changed(message, components=[]) is True


async def test_single_embed_kwarg_is_compared_too():
    embed = Embed(title="même")
    message = FakeMessage(embeds=[_rendered(embed)])

    assert await edit_message_if_changed(message, embed=embed) is False
    assert await edit_message_if_changed(message, embed=Embed(title="autre")) is True


# ── Step 1 fallbacks: bias towards editing ──────────────────────────────


async def test_attachments_force_an_edit():
    """A freshly rendered image is opaque to us, so never skip on its account."""
    embed = Embed(title="Stats")
    message = FakeMessage(embeds=[_rendered(embed)])

    assert await edit_message_if_changed(message, embeds=[embed], file=object()) is True


async def test_undiffable_payload_forces_an_edit():
    message = FakeMessage()
    assert await edit_message_if_changed(message, flags=64) is True


async def test_force_skips_the_diff():
    embed = Embed(title="Stats")
    message = FakeMessage(embeds=[_rendered(embed)])

    assert await edit_message_if_changed(message, embeds=[embed], force=True) is True


# ── Normalisation ───────────────────────────────────────────────────────


async def test_cdn_attachment_url_matches_attachment_scheme():
    """Discord rewrites ``attachment://`` to a CDN link on the way back."""
    local = Embed(title="Stats")
    local.set_image("attachment://stats.png")

    echoed = Embed.from_dict(
        {
            "title": "Stats",
            "image": {
                "url": "https://cdn.discordapp.com/attachments/1/2/stats.png?ex=abc&hm=def",
                "proxy_url": "https://media.discordapp.net/attachments/1/2/stats.png",
                "width": 800,
                "height": 600,
            },
        }
    )
    message = FakeMessage(embeds=[echoed])

    assert await edit_message_if_changed(message, embeds=[local]) is False


async def test_foreign_image_url_is_left_alone():
    local = Embed(title="Stats")
    local.set_image("https://example.com/stats.png")
    message = FakeMessage(embeds=[_rendered(local)])

    assert await edit_message_if_changed(message, embeds=[local]) is False

    other = Embed(title="Stats")
    other.set_image("https://example.com/other.png")
    assert await edit_message_if_changed(message, embeds=[other]) is True


async def test_ignore_timestamp_skips_refresh_only_renders():
    old = Embed(title="Stats", timestamp="2024-01-01T00:00:00+00:00")
    new = Embed(title="Stats", timestamp="2024-06-01T12:00:00+00:00")
    message = FakeMessage(embeds=[_rendered(old)])

    assert await edit_message_if_changed(message, embeds=[new]) is True
    message.edit_calls.clear()
    assert await edit_message_if_changed(message, embeds=[new], ignore_timestamp=True) is False


async def test_ignore_timestamp_still_sees_real_changes():
    old = Embed(title="Stats", description="42 joueurs", timestamp="2024-01-01T00:00:00+00:00")
    new = Embed(title="Stats", description="43 joueurs", timestamp="2024-06-01T12:00:00+00:00")
    message = FakeMessage(embeds=[_rendered(old)])

    assert await edit_message_if_changed(message, embeds=[new], ignore_timestamp=True) is True


# ── Steps 2 & 3: unarchive, then edit ───────────────────────────────────


async def test_archived_thread_is_unarchived_before_the_edit():
    order: list[str] = []

    class RecordingThread(FakeThread):
        async def edit(self, **kwargs):
            order.append("unarchive")
            await FakeThread.edit(self, **kwargs)

    class RecordingMessage(FakeMessage):
        async def edit(self, **kwargs):
            order.append("edit")
            await FakeMessage.edit(self, **kwargs)

    thread = RecordingThread(archived=True)
    message = RecordingMessage(content="ancien", channel=thread)

    assert await edit_message_if_changed(message, content="nouveau") is True
    assert order == ["unarchive", "edit"]
    assert thread.edit_calls[0]["archived"] is False
    assert thread.archived is False


async def test_live_thread_is_not_touched():
    thread = FakeThread(archived=False)
    message = FakeMessage(content="ancien", channel=thread)

    assert await edit_message_if_changed(message, content="nouveau") is True
    assert thread.edit_calls == []


async def test_plain_channel_is_not_touched():
    class PlainChannel:
        archived = True  # never consulted: not a thread

    message = FakeMessage(content="ancien", channel=PlainChannel())
    assert await edit_message_if_changed(message, content="nouveau") is True


async def test_failing_unarchive_does_not_block_the_edit(caplog):
    class BrokenThread(FakeThread):
        async def edit(self, **kwargs):
            raise RuntimeError("thread locked")

    message = FakeMessage(content="ancien", channel=BrokenThread(archived=True))

    assert await edit_message_if_changed(message, content="nouveau") is True
    assert message.edit_calls == [{"content": "nouveau"}]
