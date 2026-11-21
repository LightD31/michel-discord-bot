"""Welcome: the join/leave messages reach the channel, and the card renders."""

import asyncio
import importlib
import sys
from io import BytesIO
from types import SimpleNamespace

import pytest
from discord_fakes import FakeChannel, FakeGuild, FakeMember, FakeRole, bare_extension
from interactions import Permissions
from PIL import Image

from features.welcome import greeting_line, render_welcome_card, with_greetings
from features.welcome.card import CARD_HEIGHT, CARD_WIDTH

GUILD = "42"


@pytest.fixture
def welcome(store_config):
    settings = {"enabled": True, "welcomeChannelId": "7", "welcomeImageEnabled": False}
    store_config({"servers": {GUILD: {"moduleWelcome": settings}}})
    configs = {GUILD: settings}  # the store's own dicts: a test edits them in place
    sys.modules.pop("extensions.welcome", None)
    module = importlib.import_module("extensions.welcome")
    yield module, configs
    sys.modules.pop("extensions.welcome", None)


def event(*, channel_id=7, system=False, member=None, count=12):
    guild = FakeGuild(int(GUILD), "Les Copains")
    guild.member_count = count
    channel = guild.add_channel(FakeChannel(channel_id, "bienvenue"))
    guild.system_channel = channel if system else None
    member = member or guild.add_member(FakeMember(5, "bob"))
    member.display_name = "Bobby"
    return SimpleNamespace(guild=guild, member=member), channel


def run(module, listener, ev):
    ext = bare_extension(module.WelcomeExtension)
    asyncio.run(getattr(module.WelcomeExtension, listener).callback(ext, ev))


def buttons(message) -> list[str]:
    """Each button under *message*: its label, or its link."""
    rows = message.sent_with.get("components") if hasattr(message, "sent_with") else None
    rows = rows if rows is not None else message.components
    return [b.url or b.label for row in rows or [] for b in row.components]


@pytest.fixture(autouse=True)
def no_database(welcome, monkeypatch):
    opened = []

    class Greetings:
        def __init__(self, guild_id):
            self.guild_id = guild_id

        async def open(self, message_id, member_id, now):
            opened.append((self.guild_id, member_id))

    monkeypatch.setattr("extensions.welcome.GreetingRepository", Greetings, raising=False)
    return opened


def test_default_messages_name_the_member(welcome, no_database):
    module, _ = welcome
    ev, channel = event()
    run(module, "on_member_add", ev)
    run(module, "on_member_remove", ev)
    assert [m.content for m in channel.sent] == ["Bienvenue <@5> !", "bob nous a quittés."]
    # By default: /aide and the greeting button under the welcome, none under the leave.
    assert buttons(channel.sent[0]) == ["Que faire ici ?", "Souhaiter la bienvenue"]
    assert buttons(channel.sent[1]) == []
    assert no_database == [(int(GUILD), "5")]


def test_the_new_variables_name_the_server_and_its_size(welcome):
    module, configs = welcome
    configs[GUILD]["welcomeMessageList"] = [
        {"text": "{pseudo}, tu es le {nombre}ᵉ sur {serveur} !", "weight": 1}
    ]
    ev, channel = event()
    run(module, "on_member_add", ev)
    assert channel.sent[0].content == "Bobby, tu es le 12ᵉ sur Les Copains !"


def test_the_leave_message_fills_username_as_the_dashboard_documents(welcome):
    module, configs = welcome
    configs[GUILD]["leaveMessageList"] = [{"text": "Au revoir {username} 👋", "weight": 1}]
    ev, channel = event()
    run(module, "on_member_remove", ev)
    assert channel.sent[0].content == "Au revoir bob 👋"


def test_the_system_channel_is_used_when_none_is_configured(welcome):
    module, configs = welcome
    del configs[GUILD]["welcomeChannelId"]
    ev, system = event(channel_id=8, system=True)
    run(module, "on_member_add", ev)
    assert [m.content for m in system.sent] == ["Bienvenue <@5> !"]


def test_no_channel_at_all_is_skipped_without_error(welcome):
    module, _ = welcome
    ev, elsewhere = event(channel_id=8)  # the configured 7 isn't in the cache
    run(module, "on_member_add", ev)
    assert elsewhere.sent == []  # no other channel picked instead


def test_the_links_the_role_and_the_dm_copy(welcome, no_database):
    module, configs = welcome
    ev, channel = event()
    arrival = ev.guild.add_role(FakeRole(30, "Nouveau"))
    configs[GUILD].update(
        welcomeRulesChannelId="70",
        welcomeRolesChannelId="71",
        welcomeArrivalRoleId=str(arrival.id),
        welcomeGreetButton=False,
        welcomeDmCopy=True,
    )
    run(module, "on_member_add", ev)
    rules, roles = (f"https://discord.com/channels/{GUILD}/{c}" for c in ("70", "71"))
    assert buttons(channel.sent[0]) == [rules, roles, "Que faire ici ?"]
    assert no_database == []  # no greeting button, nothing to count
    assert ev.member.has_role(30)
    (dm,) = ev.member.dm_messages
    assert dm.content == "-# Depuis Les Copains\nBienvenue <@5> !"
    assert buttons(dm) == [rules, roles]  # in a DM, only the links


def test_a_staff_arrival_role_is_never_given(welcome):
    module, configs = welcome
    ev, channel = event()
    staff = ev.guild.add_role(FakeRole(31, "Modo", permissions=Permissions.BAN_MEMBERS))
    configs[GUILD]["welcomeArrivalRoleId"] = str(staff.id)
    run(module, "on_member_add", ev)
    assert not ev.member.has_role(31)
    assert channel.sent  # the welcome itself still goes out


def test_closed_dms_dont_stop_the_welcome(welcome):
    module, configs = welcome
    configs[GUILD]["welcomeDmCopy"] = True
    ev, channel = event()
    ev.member.dms_closed = True
    run(module, "on_member_add", ev)
    assert channel.sent[0].content == "Bienvenue <@5> !"


@pytest.mark.parametrize(
    ("names", "total", "line"),
    [
        (["Léa"], 1, "-# 👋 Léa te souhaite la bienvenue"),
        (["Léa", "Hugo"], 2, "-# 👋 Léa et Hugo te souhaitent la bienvenue"),
        (
            ["A", "B", "C", "D", "E", "F"],
            6,
            "-# 👋 A, B, C, D, E et 1 autre te souhaitent la bienvenue",
        ),
        (["", ""], 2, "-# 👋 2 personnes te souhaitent la bienvenue"),
    ],
)
def test_the_greeting_line_names_the_first_and_counts_the_rest(names, total, line):
    assert greeting_line(names, total) == line


def test_the_greeting_line_replaces_the_previous_one():
    once = with_greetings("Bienvenue <@5> !", greeting_line(["Léa"], 1))
    assert once == "Bienvenue <@5> !\n\n-# 👋 Léa te souhaite la bienvenue"
    twice = with_greetings(once, greeting_line(["Léa", "Hugo"], 2))
    assert twice == "Bienvenue <@5> !\n\n-# 👋 Léa et Hugo te souhaitent la bienvenue"


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
