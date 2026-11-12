"""Commands anyone can install, in the member's language.

``/pick``, ``/roll``, ``/coin``, ``/shuffle``, ``/ask`` and ``/ask-stats`` can
be installed by any Discord user, who may not read French. Their names,
descriptions and options are written in English, Discord's fallback for every
client language, with a French localisation that French clients see; their
replies follow the language of whoever ran them (:func:`say`). The rest of the
bot serves French communities and stays in French.
"""

from __future__ import annotations

from typing import Any

from interactions import LocalisedDesc, LocalisedName


def en_fr_name(en: str, fr: str) -> LocalisedName:
    """A command or option name: *en* by default, *fr* for French clients."""
    return LocalisedName(english_us=en, french=fr)


def en_fr_desc(en: str, fr: str) -> LocalisedDesc:
    """A description or choice label: *en* by default, *fr* for French clients."""
    return LocalisedDesc(english_us=en, french=fr)


def is_french(ctx: Any) -> bool:
    """Whether the member who triggered *ctx* uses Discord in French (or it can't be told)."""
    locale = getattr(ctx, "locale", None)
    return not locale or str(locale).startswith("fr")


def say(ctx: Any, *, fr: str, en: str) -> str:
    """*fr* for a French client, *en* for any other."""
    return fr if is_french(ctx) else en


__all__ = ["en_fr_desc", "en_fr_name", "is_french", "say"]
