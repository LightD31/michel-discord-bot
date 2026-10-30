"""Account fields validated by /register."""

import string

import pytest

from features.lldap import (
    generate_temp_password,
    normalize_email,
    normalize_username,
    username_from_discord,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Alice@Example.COM ", "alice@example.com"),
        ("a.b+tag@mail.example.org", "a.b+tag@mail.example.org"),
        ("no-at-sign.example.com", None),
        ("two@@example.com", None),
        ("alice@localhost", None),
        ("spaces in@example.com", None),
        ("", None),
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alice", "alice"),
        (" bob.smith_42 ", "bob.smith_42"),
        ("a-b", "a-b"),
        ("ab", None),
        ("x" * 33, None),
        ("x" * 32, "x" * 32),
        ("_leading", None),
        ("has space", None),
        ("émile", None),
    ],
)
def test_normalize_username(raw, expected):
    assert normalize_username(raw) == expected


@pytest.mark.parametrize(
    ("discord_name", "expected"),
    [
        ("alice", "alice"),
        ("Alice.Smith_", "alice.smith_"),
        ("__cool__", "cool__"),
        ("émile", "mile"),
        ("éé", None),
        ("a" * 40, "a" * 32),
    ],
)
def test_username_from_discord(discord_name, expected):
    assert username_from_discord(discord_name) == expected


def test_temp_password_uses_letters_and_digits():
    password = generate_temp_password()
    assert len(password) == 16
    assert set(password) <= set(string.ascii_letters + string.digits)
    assert generate_temp_password(24) != generate_temp_password(24)
    assert len(generate_temp_password(24)) == 24
