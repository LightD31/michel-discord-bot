"""Reactions refresh reaction polls only, and the refresh keeps every option's text."""

from types import SimpleNamespace

import pytest
from interactions import Embed

import extensions.polls as polls_module
import src.discord_ext.paginator as paginator_module
from extensions.polls import PollsExtension
from features.polls import is_reaction_poll_text, render_reaction_poll
from features.polls.helpers import poll_option_label
from src.discord_ext.embeds import Colors

BOT_ID = 99
POLL_FOOTER = "Créé par alice (ID: 123)"
POLL_BODY = "1️⃣ Pizza : 4 fromages\n\n2️⃣ Sushi\n\n*Fermeture automatique : <t:1700000000:R>*"


# --- pure helpers ---------------------------------------------------------


def test_render_keeps_colons_and_the_deadline_note():
    rendered = render_reaction_poll(POLL_BODY, {"1️⃣": ["Bob", "Eve"], "2️⃣": ["Max"]}, 3)
    assert rendered == (
        "**1️⃣ Pizza : 4 fromages : 2/3 votes\n(Bob, Eve)**\n\n"
        "2️⃣ Sushi : **1/3 votes**\n(Max)\n\n"
        "*Fermeture automatique : <t:1700000000:R>*"
    )


def test_render_is_stable_across_successive_reactions():
    once = render_reaction_poll(POLL_BODY, {"1️⃣": ["Bob"], "2️⃣": ["Max", "Eve"]}, 3)
    twice = render_reaction_poll(once, {"1️⃣": ["Bob"], "2️⃣": ["Max", "Eve"]}, 3)
    assert twice == once
    cleared = render_reaction_poll(twice, {}, 0)
    assert cleared == POLL_BODY


@pytest.mark.parametrize(
    ("block", "label"),
    [
        ("1️⃣ Heure : 20h", "1️⃣ Heure : 20h"),
        ("**1️⃣ Heure : 20h : 2/3 votes\n(A, B)**", "1️⃣ Heure : 20h"),
        ("1️⃣ Heure : 20h : **1/3 votes**\n(A)", "1️⃣ Heure : 20h"),
        ("1️⃣ **gras**", "1️⃣ **gras**"),
    ],
)
def test_option_label_strips_only_the_tally(block, label):
    assert poll_option_label(block) == label


@pytest.mark.parametrize(
    ("footer", "description", "expected"),
    [
        (POLL_FOOTER, POLL_BODY, True),
        (POLL_FOOTER, "👍 Oui\n\n👎 Non", True),
        # RSS post with the default colour
        (
            "Flux : Le Monde",
            "**Nouvel article : Sortie de la v2**\n\nLire la suite : https://x",
            False,
        ),
        # role menu
        (None, "<:pepe:123456> <@&42> — Joueurs\n👍 <@&43> — Lecteurs", False),
        # button poll
        (POLL_FOOTER, "*Sondage anonyme* — cliquez sur un bouton pour voter.", False),
        # giveaway
        ("Lancé par alice", "1️⃣ gagnant", False),
    ],
)
def test_reaction_poll_recognition(footer, description, expected):
    assert is_reaction_poll_text(footer, description) is expected


# --- listener -------------------------------------------------------------


class FakeReaction:
    def __init__(self, emoji, users):
        self.emoji = emoji
        self._users = users

    def users(self):
        users = self._users

        class _Iter:
            async def flatten(self):
                return list(users)

        return _Iter()


def user(uid, name, bot=False):
    return SimpleNamespace(id=uid, display_name=name, bot=bot)


def message(embed, *, author_id=BOT_ID, reactions=()):
    return SimpleNamespace(
        id=10,
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=author_id),
        embeds=[embed],
        reactions=list(reactions),
    )


class RecordingRefreshes:
    """Stands for the Coalescer: records what would be refreshed."""

    def __init__(self):
        self.requests = []

    def request(self, key, refresh):
        self.requests.append((key, refresh))

    def pending(self):
        return len(self.requests)


@pytest.fixture
def ext(monkeypatch, store_config):
    store_config({"servers": {"1": {"moduleUtils": {"enabled": True}}}})
    monkeypatch.setattr(paginator_module, "_guild_name_overrides", lambda server_id: {})
    edits: list = []

    async def fake_edit(msg, **kwargs):
        edits.append((msg, kwargs))

    monkeypatch.setattr(polls_module, "edit_message_if_changed", fake_edit)
    extension = object.__new__(PollsExtension)  # skip interactions' Extension wiring
    extension.bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID))
    extension._refreshes = RecordingRefreshes()
    extension.edits = edits
    return extension


def poll_embed(description=POLL_BODY, footer=POLL_FOOTER):
    embed = Embed(title="Ce soir ?", description=description, color=Colors.UTIL)
    if footer:
        embed.set_footer(text=footer)
    return embed


async def test_a_reaction_on_an_rss_post_leaves_it_alone(ext):
    rss = Embed(
        title="Le Monde",
        description="**Nouvel article : Sortie de la v2**\n\nLire la suite : https://x",
        color=Colors.UTIL,
    )
    rss.set_footer(text="Flux : Le Monde")
    event = SimpleNamespace(message=message(rss), emoji="👍")
    ext._refresh_reaction_poll(event)
    assert ext._refreshes.pending() == 0


async def test_a_reaction_on_a_role_menu_leaves_it_alone(ext):
    menu = Embed(title="Rôles", description="<:pepe:123456> <@&42> — Joueurs", color=Colors.UTIL)
    event = SimpleNamespace(message=message(menu), emoji="<:pepe:123456>")
    ext._refresh_reaction_poll(event)
    assert ext._refreshes.pending() == 0


async def test_a_poll_posted_by_someone_else_is_ignored(ext):
    event = SimpleNamespace(message=message(poll_embed(), author_id=5), emoji="1️⃣")
    ext._refresh_reaction_poll(event)
    assert ext._refreshes.pending() == 0


async def test_a_reaction_outside_the_options_is_ignored(ext):
    event = SimpleNamespace(message=message(poll_embed()), emoji="😂")
    ext._refresh_reaction_poll(event)
    assert ext._refreshes.pending() == 0


async def test_polls_off_in_the_server_means_no_refresh(ext, store_config):
    store_config({"servers": {"1": {"moduleUtils": {"enabled": False}}}})
    event = SimpleNamespace(message=message(poll_embed()), emoji="1️⃣")
    ext._refresh_reaction_poll(event)
    assert ext._refreshes.pending() == 0


async def test_a_vote_refreshes_the_poll(ext):
    reactions = [
        FakeReaction("1️⃣", [user(BOT_ID, "Michel", bot=True), user(1, "Bob")]),
        FakeReaction("2️⃣", [user(BOT_ID, "Michel", bot=True)]),
        FakeReaction("😂", [user(2, "Eve")]),  # not an option: neither read nor counted
    ]
    event = SimpleNamespace(message=message(poll_embed(), reactions=reactions), emoji="1️⃣")
    ext._refresh_reaction_poll(event)
    [(key, refresh)] = ext._refreshes.requests
    assert key == 10
    await refresh()  # what the coalesced refresh runs, a few seconds later
    [(_, kwargs)] = ext.edits
    assert kwargs["embed"].description == (
        "**1️⃣ Pizza : 4 fromages : 1/1 votes\n(Bob)**\n\n"
        "2️⃣ Sushi\n\n"
        "*Fermeture automatique : <t:1700000000:R>*"
    )
