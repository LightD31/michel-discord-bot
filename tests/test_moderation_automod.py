"""The anti-spam rule reads its settings within bounds, whatever config.json says."""

from types import SimpleNamespace

import pytest
from discord_fakes import bare_extension

from extensions.moderation import ModerationExtension
from features.moderation import SpamTracker, spam_rule


def message(author_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=author_id))


def automod() -> ModerationExtension:
    ext = bare_extension(ModerationExtension)
    ext._spam = SpamTracker()
    return ext


@pytest.mark.parametrize("threshold", [-3, 0, 1, "1"])
def test_a_threshold_below_two_does_not_make_a_first_message_spam(threshold):
    """``int(x or 5)`` only replaced 0: a threshold of 1 or less deleted every message (Q1)."""
    ext = automod()
    settings = {"antiSpam": True, "spamThreshold": threshold, "spamWindowSeconds": 7}
    assert ext._track_spam(message(), settings) is False


def test_the_configured_threshold_still_triggers():
    ext = automod()
    settings = {"antiSpam": True, "spamThreshold": 3, "spamWindowSeconds": 7}
    assert [ext._track_spam(message(), settings) for _ in range(3)] == [False, False, True]


@pytest.mark.parametrize("window", [-5, 0, "x"])
def test_an_unusable_window_falls_back_to_a_working_one(window):
    ext = automod()
    settings = {"antiSpam": True, "spamThreshold": 2, "spamWindowSeconds": window}
    assert [ext._track_spam(message(), settings) for _ in range(2)] == [False, True]


def test_the_rule_is_clamped_at_both_ends():
    assert spam_rule({}) == (5, 7)
    assert spam_rule({"spamThreshold": 1000, "spamWindowSeconds": 10**6}) == (50, 600)
    assert spam_rule({"spamThreshold": None, "spamWindowSeconds": "3"}) == (5, 3)
