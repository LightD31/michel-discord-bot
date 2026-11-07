"""What the bot is working on, attached to every log line written meanwhile.

A command, a button click or a task run binds its guild, its name and a
short correlation id; every record logged while it runs carries them, in the
JSON logs as fields and in the text logs as a suffix. The correlation id is
also the reference a member is shown when their interaction fails
(``extensions/errors.py``), so a report leads straight to the matching lines.

The values live in :mod:`contextvars`: an ``asyncio`` task inherits them from
whoever created it, and they never leak between concurrent interactions.

Usage::

    from src.core import log_context

    with log_context.scope(task="youtube.check", correlation_id=log_context.new_id()):
        ...  # every log line in here carries task= and correlation_id=
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

FIELDS = ("correlation_id", "guild_id", "command", "task")

_vars: dict[str, ContextVar[str | None]] = {
    name: ContextVar(f"michel_log_{name}", default=None) for name in FIELDS
}


def new_id() -> str:
    """A short random id: six hex characters, easy to read out and to grep."""
    return secrets.token_hex(3)


def bind(**fields: object) -> dict[str, Token[str | None]]:
    """Set fields for the rest of the current context; returns the tokens for :func:`reset`.

    ``None`` clears a field. Unknown field names raise ``KeyError``.
    """
    return {
        name: _vars[name].set(None if value is None else str(value))
        for name, value in fields.items()
    }


def reset(tokens: dict[str, Token[str | None]]) -> None:
    """Undo a :func:`bind`."""
    for name, token in tokens.items():
        _vars[name].reset(token)


@contextmanager
def scope(**fields: object) -> Iterator[None]:
    """Bind *fields* for the duration of the ``with`` block."""
    tokens = bind(**fields)
    try:
        yield
    finally:
        reset(tokens)


def current() -> dict[str, str]:
    """The fields set in this context (unset ones are left out)."""
    values = {name: var.get() for name, var in _vars.items()}
    return {name: value for name, value in values.items() if value is not None}


def correlation_id() -> str | None:
    """The current correlation id, if something bound one."""
    return _vars["correlation_id"].get()


__all__ = ["FIELDS", "bind", "correlation_id", "current", "new_id", "reset", "scope"]
