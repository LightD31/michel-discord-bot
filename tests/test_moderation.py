"""Unit tests for the pure moderation domain layer (no DB / Discord)."""

import pytest

from features.moderation import (
    MAX_TIMEOUT_SECONDS,
    Infraction,
    clamp_timeout,
    contains_invite,
    humanize_duration,
    match_banned_word,
    parse_duration,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45s", 45),
        ("10m", 600),
        ("1h", 3600),
        ("2d", 172800),
        ("1h30m", 5400),
        ("1h 30m", 5400),  # whitespace between tokens
        ("10", 600),  # bare integer → minutes
        ("", None),
        ("abc", None),
        ("1x", None),  # unknown unit
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_clamp_timeout_bounds():
    assert clamp_timeout(0) == 1
    assert clamp_timeout(60) == 60
    assert clamp_timeout(99_999_999) == MAX_TIMEOUT_SECONDS
    assert MAX_TIMEOUT_SECONDS == 28 * 86400


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 s"),
        (45, "45 s"),
        (90, "1 min 30 s"),
        (3600, "1 h"),
        (93784, "1 j 2 h 3 min 4 s"),
    ],
)
def test_humanize_duration(seconds, expected):
    assert humanize_duration(seconds) == expected


def test_infraction_alias_roundtrip():
    # Mongo documents arrive with "_id"; the model exposes it as object_id.
    inf = Infraction(
        **{
            "_id": "abc123",
            "id": 7,
            "guild_id": "1",
            "user_id": "2",
            "moderator_id": "3",
            "type": "warn",
            "created_at": "2026-01-01T00:00:00",
        }
    )
    assert inf.object_id == "abc123"
    assert inf.active is True  # default
    assert inf.source == "manual"  # default
    # object_id is excluded from the persisted payload (Mongo mints _id itself).
    assert "object_id" not in inf.model_dump(exclude={"object_id"})


def test_match_banned_word():
    words = ["spam", "Foo"]
    assert match_banned_word("this is SPAM here", words) == "spam"
    assert match_banned_word("a foo walks in", words) == "Foo"
    assert match_banned_word("foobar is fine", words) is None  # word boundary
    assert match_banned_word("nothing here", words) is None
    assert match_banned_word("anything", []) is None


def test_contains_invite():
    assert contains_invite("join here discord.gg/abcd")
    assert contains_invite("https://discord.com/invite/xyz now")
    assert contains_invite("discordapp.com/invite/foo")
    assert not contains_invite("just a normal message")
    assert not contains_invite("")
