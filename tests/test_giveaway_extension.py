"""The giveaway draw: one draw per giveaway, retried on an outage, host kept in the footer."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from interactions import Embed

import extensions.giveaway as giveaway_module
from extensions.giveaway import MAX_DRAW_FAILURES, GiveawayExtension
from features.giveaway import Giveaway


class FakeRepo:
    def __init__(self, claimable=True):
        self.claimable = claimable
        self.released = 0
        self.winners = None

    async def claim_draw(self, giveaway_id):
        claimed, self.claimable = self.claimable, False
        return claimed

    async def release_draw(self, giveaway_id):
        self.released += 1
        self.claimable = True

    async def update_winners(self, giveaway_id, winners):
        self.winners = winners


class FakeUsers:
    def __init__(self, users):
        self._users = users

    def __aiter__(self):
        async def gen():
            for user in self._users:
                yield user

        return gen()


def announcement(entrant_ids):
    embed = Embed(title="🎁 GIVEAWAY — Un jeu")
    embed.set_footer(text="Lancé par alice", icon_url="https://cdn/alice.png")
    users = [SimpleNamespace(id=int(uid), bot=False) for uid in entrant_ids]
    reaction = SimpleNamespace(emoji="🎉", users=lambda: FakeUsers(users))
    replies = []

    async def reply(text):
        replies.append(text)

    return SimpleNamespace(id=5, embeds=[embed], reactions=[reaction], reply=reply, replies=replies)


def giveaway(**kw):
    return Giveaway(
        _id="a" * 24,
        guild_id="1",
        channel_id="2",
        message_id="5",
        host_id="3",
        prize="Un jeu",
        allow_host_win=False,
        ends_at=datetime.now() - timedelta(minutes=1),
        **kw,
    )


@pytest.fixture
def ext(monkeypatch):
    edits = []

    async def fake_edit(message, **kwargs):
        edits.append(kwargs)

    monkeypatch.setattr(giveaway_module, "edit_message_if_changed", fake_edit)
    extension = object.__new__(GiveawayExtension)  # skip interactions' Extension wiring
    extension.repo = FakeRepo()
    extension._repo = lambda guild_id: extension.repo
    extension.edits = edits
    return extension


def channel_with(message=None, error=None):
    async def fetch_message(message_id):
        if error:
            raise error
        return message

    return SimpleNamespace(fetch_message=fetch_message)


def bot_with(channel):
    async def fetch_channel(channel_id):
        return channel

    return SimpleNamespace(fetch_channel=fetch_channel)


async def test_a_draw_lost_to_another_one_touches_nothing(ext):
    ext.repo.claimable = False
    ext.bot = bot_with(channel_with(announcement(["10"])))
    assert await ext._draw(giveaway()) == "taken"
    assert ext.edits == [] and ext.repo.winners is None


async def test_the_draw_keeps_the_host_in_the_footer(ext):
    message = announcement(["10", "11"])
    ext.bot = bot_with(channel_with(message))
    assert await ext._draw(giveaway()) == "drawn"
    assert len(ext.repo.winners) == 1 and ext.repo.winners[0] in {"10", "11"}
    [edit] = ext.edits
    footer = edit["embeds"][0].footer
    assert footer.text == "Lancé par alice" and footer.icon_url == "https://cdn/alice.png"
    assert message.replies and "Félicitations" in message.replies[0]


async def test_an_outage_gives_the_draw_back(ext):
    ext.bot = bot_with(channel_with(error=RuntimeError("503")))
    assert await ext._draw(giveaway()) == "retry"
    assert ext.repo.released == 1 and ext.repo.winners is None and ext.edits == []


async def test_after_repeated_outages_the_giveaway_closes(ext):
    ext.bot = bot_with(channel_with(error=RuntimeError("503")))
    outcome = await ext._draw(giveaway(draw_failures=MAX_DRAW_FAILURES - 1))
    assert outcome == "drawn" and ext.repo.released == 0


async def test_a_deleted_announcement_closes_without_winners(ext):
    ext.bot = bot_with(None)  # fetch_channel answers None: the channel is gone
    assert await ext._draw(giveaway()) == "drawn"
    assert ext.repo.released == 0 and ext.edits == []
