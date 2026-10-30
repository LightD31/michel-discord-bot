"""Account fields for ``/register``: email, username, temporary password."""

from __future__ import annotations

import re
import secrets
import string

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 32
PASSWORD_LENGTH = 16

_USERNAME_RE = re.compile(
    rf"[a-z0-9][a-z0-9._-]{{{USERNAME_MIN_LENGTH - 1},{USERNAME_MAX_LENGTH - 1}}}"
)
_USERNAME_DISALLOWED = re.compile(r"[^a-z0-9._-]")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def normalize_email(raw: str) -> str | None:
    """The lowercased email, or ``None`` when it doesn't look like one."""
    email = raw.strip().lower()
    return email if len(email) <= 254 and _EMAIL_RE.fullmatch(email) else None


def normalize_username(raw: str) -> str | None:
    """The lowercased LLDAP user id, or ``None`` when it breaks the rules.

    3 to 32 letters, digits, ``.``, ``_`` or ``-``, starting with a letter or
    digit, so it is also a safe LDAP ``uid``.
    """
    username = raw.strip().lower()
    return username if _USERNAME_RE.fullmatch(username) else None


def username_from_discord(name: str) -> str | None:
    """A user id derived from a Discord username, ``None`` if too little is left."""
    cleaned = _USERNAME_DISALLOWED.sub("", name.lower()).lstrip("._-")
    return normalize_username(cleaned[:USERNAME_MAX_LENGTH])


def generate_temp_password(length: int = PASSWORD_LENGTH) -> str:
    """A random password of letters and digits, from a cryptographic source."""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
