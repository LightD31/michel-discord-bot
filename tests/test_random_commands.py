"""/pick and /shuffle echo safely, and the commands anyone installs speak their language."""

import random

import pytest

from extensions.random_ import NO_PINGS, RandomExtension
from src.discord_ext.locale import is_french


class FakeCtx:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


async def test_pick_echoes_without_pinging():
    ctx = FakeCtx()
    await RandomExtension.pick.callback(object(), ctx, "@everyone;@here", ";")
    [(content, kwargs)] = ctx.sent
    assert "@" in content and kwargs["allowed_mentions"] is NO_PINGS


async def test_shuffle_stays_within_a_message():
    ctx = FakeCtx()
    items = ";".join("x" * 14 for _ in range(100))  # 1500 characters, the option's cap
    await RandomExtension.shuffle.callback(object(), ctx, items, ";")
    [(content, kwargs)] = ctx.sent
    assert len(content) <= 2000 and kwargs["allowed_mentions"] is NO_PINGS


# Localised for whoever installs them (F05) --------------------------------------


class LocaleCtx(FakeCtx):
    def __init__(self, locale):
        super().__init__()
        self.locale = locale


@pytest.mark.parametrize(("locale", "french"), [("fr", True), (None, True), ("en-US", False)])
def test_the_reply_language_follows_the_members_client(locale, french):
    assert is_french(LocaleCtx(locale)) is french


async def test_pick_answers_in_english_outside_french_clients(monkeypatch):
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])
    english, french = LocaleCtx("en-GB"), LocaleCtx("fr")
    await RandomExtension.pick.callback(object(), english, "a;a", ";")
    await RandomExtension.pick.callback(object(), french, "a;a", ";")
    assert english.sent[0][0] == "I choose: **a**"
    assert french.sent[0][0] == "Mhh, random.org a chosi : **a**"


async def test_errors_and_coin_follow_the_language():
    english = LocaleCtx("en-US")
    await RandomExtension.pick.callback(object(), english, "seul", ";")
    await RandomExtension.roll.callback(object(), english, 1)
    await RandomExtension.coin.callback(object(), english)
    texts = [content for content, _ in english.sent]
    assert texts[0].startswith("🤔 Hard to choose") and "faces!" in texts[1]
    assert texts[2] in {"🪙 **Heads**", "🪙 **Tails**"}


def test_the_commands_are_described_in_english_with_a_french_localisation():
    for command in (RandomExtension.pick, RandomExtension.roll, RandomExtension.coin):
        payload = command.to_dict()
        assert payload["description_localizations"]["fr"]
        assert payload["description"] != payload["description_localizations"]["fr"]
    (choices, separator) = RandomExtension.pick.to_dict()["options"]
    assert (choices["name"], choices["name_localizations"]) == ("choices", {"fr": "choix"})
    assert separator["name_localizations"] == {"fr": "séparateur"}
