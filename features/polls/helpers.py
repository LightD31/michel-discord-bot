"""Pure (Discord-agnostic) helpers for ``/sondage``: its options and its duration."""

import math

from features.polls.constants import DEFAULT_POLL_OPTIONS

MAX_POLL_OPTIONS = 10
# Discord's own polls: an answer of 55 characters at most, open 1 hour to 32 days.
NATIVE_ANSWER_LIMIT = 55
NATIVE_MIN_HOURS = 1
NATIVE_MAX_HOURS = 32 * 24
NATIVE_DEFAULT_HOURS = 24
# A button's label (the vote buttons of anonymous and ranked polls).
BUTTON_OPTION_LIMIT = 70
# A poll's footer names its author, after Michel's: "Michel · Utilitaires · Sondage de bob".
POLL_FOOTER_MARK = "Sondage de "


def poll_footer(author_name: str) -> str:
    """What a poll adds to Michel's footer: who asked (by name, never by id)."""
    return f"{POLL_FOOTER_MARK}{author_name}"


def parse_options(text: str, *, limit: int, default_yes_no: bool = False) -> list[str]:
    """The options a member typed in the form, one per line; ``ValueError`` (French) otherwise.

    Blank lines are skipped and a repeated option kept once. A public poll
    left empty is a Oui / Non (*default_yes_no*).
    """
    options: list[str] = []
    for line in text.splitlines():
        option = line.strip().lstrip("-•*").strip()
        if option and option.casefold() not in {o.casefold() for o in options}:
            options.append(option)
    if not options and default_yes_no:
        return list(DEFAULT_POLL_OPTIONS)
    if len(options) < 2:
        raise ValueError("Écris au moins 2 options, une par ligne.")
    if len(options) > MAX_POLL_OPTIONS:
        raise ValueError(f"Un sondage a {MAX_POLL_OPTIONS} options au plus ({len(options)} ici).")
    too_long = next((o for o in options if len(o) > limit), None)
    if too_long:
        raise ValueError(
            f"L'option « {too_long[:20]}… » est trop longue : {limit} caractères au plus."
        )
    return options


def native_poll_hours(seconds: int | None) -> int:
    """How long a Discord poll stays open, in whole hours (what Discord takes).

    Rounded up, and kept between 1 hour and 32 days; 24 hours without a
    duration.
    """
    if not seconds:
        return NATIVE_DEFAULT_HOURS
    return max(NATIVE_MIN_HOURS, min(math.ceil(seconds / 3600), NATIVE_MAX_HOURS))


__all__ = [
    "BUTTON_OPTION_LIMIT",
    "MAX_POLL_OPTIONS",
    "NATIVE_ANSWER_LIMIT",
    "POLL_FOOTER_MARK",
    "native_poll_hours",
    "parse_options",
    "poll_footer",
]
