"""Welcome: the join/leave messages reach the channel, and the card renders."""

import asyncio
import importlib
import sys
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from features.welcome import render_welcome_card
from features.welcome.card import CARD_HEIGHT, CARD_WIDTH

GUILD = "42"


@pytest.fixture
def welcome(monkeypatch):
    import src.core.config as core_config

    configs = {GUILD: {"welcomeChannelId": "7", "welcomeImageEnabled": False}}
    monkeypatch.setattr(
        core_config, "load_config", lambda module_name=None: ({}, configs, list(configs))
    )
    sys.modules.pop("extensions.welcome", None)
    module = importlib.import_module("extensions.welcome")
    yield module, configs
    sys.modules.pop("extensions.welcome", None)


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, content, files=None):
        self.sent.append(content)


def event(channels, *, system_channel=None):
    guild = SimpleNamespace(
        id=int(GUILD), name="Serveur", system_channel=system_channel, get_channel=channels.get
    )
    member = SimpleNamespace(username="bob", mention="<@5>")
    return SimpleNamespace(guild=guild, member=member)


def run(module, listener, ev):
    ext = object.__new__(module.WelcomeExtension)  # skip interactions' Extension wiring
    asyncio.run(getattr(module.WelcomeExtension, listener).callback(ext, ev))


def test_default_messages_name_the_member(welcome):
    module, _ = welcome
    channel = Channel()
    run(module, "on_member_add", event({"7": channel}))
    run(module, "on_member_remove", event({"7": channel}))
    assert channel.sent == ["Bienvenue <@5> !", "bob nous a quittés."]


def test_the_leave_message_fills_username_as_the_dashboard_documents(welcome):
    module, configs = welcome
    configs[GUILD]["leaveMessageList"] = [{"text": "Au revoir {username} 👋", "weight": 1}]
    channel = Channel()
    run(module, "on_member_remove", event({"7": channel}))
    assert channel.sent == ["Au revoir bob 👋"]


def test_the_system_channel_is_used_when_none_is_configured(welcome):
    module, configs = welcome
    del configs[GUILD]["welcomeChannelId"]
    system = Channel()
    run(module, "on_member_add", event({}, system_channel=system))
    assert system.sent == ["Bienvenue <@5> !"]


def test_no_channel_at_all_is_skipped_without_error(welcome):
    module, _ = welcome
    run(module, "on_member_add", event({}))  # the configured channel isn't cached


def avatar_png(color=(200, 30, 30)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("avatar", [avatar_png(), None, b"not an image"])
def test_the_card_renders_whatever_the_avatar(avatar):
    card = Image.open(
        render_welcome_card(avatar_bytes=avatar, username="x" * 40, subtitle="Serveur · 12")
    )
    assert card.format == "PNG"
    assert card.size == (CARD_WIDTH, CARD_HEIGHT)


def test_the_avatar_is_drawn_on_the_card():
    card = Image.open(render_welcome_card(avatar_bytes=avatar_png((200, 30, 30)), username="bob"))
    center = (32 + 80, CARD_HEIGHT // 2)  # PADDING + AVATAR_SIZE / 2
    assert card.convert("RGB").getpixel(center) == (200, 30, 30)
