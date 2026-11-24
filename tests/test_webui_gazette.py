"""The Gazette's dashboard card: a preview, and publishing now for who can post there."""

from datetime import timedelta

import pytest
from discord_fakes import FakeBot, FakeChannel, FakeGuild, FakeMember
from fastapi import HTTPException
from interactions import Permissions

import src.webui.routes.gazette as routes
from features.gazette import Edition


@pytest.fixture
def world(store_config, monkeypatch):
    store_config({"servers": {"1": {"moduleGazette": {"enabled": True, "gazetteChannelId": "40"}}}})
    guild = FakeGuild(1, "Les Copains")
    channel = guild.add_channel(FakeChannel(40, "gazette"))
    admin = guild.add_member(
        FakeMember(3, "camille", permissions=Permissions.VIEW_CHANNEL | Permissions.SEND_MESSAGES)
    )

    async def gather(bot, guild_id, now):
        return Edition("Les Copains", now - timedelta(days=7), now, newcomers=["Hugo"])

    monkeypatch.setattr(routes, "gather_edition", gather)
    return FakeBot(guild), channel, admin


async def test_the_preview_reads_as_the_post(world):
    bot, *_ = world
    data = await routes.preview_on_bot(bot, "1")
    assert data["title"] == "📰 La Gazette de Les Copains"
    assert data["fields"] == [{"name": "👋 Bienvenue", "value": "Hugo"}]


async def test_publishing_now_posts_in_the_gazette_channel(world):
    bot, channel, admin = world
    result = await routes.publish_on_bot(bot, "1", "3")
    (post,) = channel.sent
    assert result == {"channel": "gazette", "message_id": str(post.id)}


async def test_publishing_needs_the_right_to_post_there(world):
    bot, channel, admin = world
    channel.overrides[3] = Permissions.VIEW_CHANNEL
    with pytest.raises(HTTPException) as refused:
        await routes.publish_on_bot(bot, "1", "3")
    assert refused.value.status_code == 403 and channel.sent == []


async def test_publishing_needs_the_module_on(world, store_config):
    bot, channel, _ = world
    store_config({"servers": {}})
    with pytest.raises(HTTPException) as refused:
        await routes.publish_on_bot(bot, "1", "3")
    assert refused.value.status_code == 409
