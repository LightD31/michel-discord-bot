"""Duration parsing/formatting for moderation timeouts.

Pure helpers — no Discord or DB imports — so they are trivially unit-testable.
"""

import re

# Seconds per supported unit suffix.
_UNIT_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}

# Discord caps member timeouts at 28 days.
MAX_TIMEOUT_SECONDS = 28 * 86400

_TOKEN_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)


def parse_duration(text: str) -> int | None:
    """Parse a human duration like ``10m``, ``1h30m``, ``2d`` into seconds.

    A bare integer is interpreted as minutes (``"10"`` → 600). Returns ``None``
    when *text* contains no recognizable ``<number><unit>`` token. The result is
    never negative; callers should still :func:`clamp_timeout` before use.
    """
    if not text:
        return None
    text = text.strip().lower()
    if text.isdigit():
        return int(text) * 60
    total = 0
    matched = False
    for value, unit in _TOKEN_RE.findall(text):
        matched = True
        total += int(value) * _UNIT_SECONDS[unit.lower()]
    return total if matched else None


def clamp_timeout(seconds: int) -> int:
    """Clamp a timeout to Discord's allowed range ``[1, 28 days]``."""
    return max(1, min(seconds, MAX_TIMEOUT_SECONDS))


def humanize_duration(seconds: int) -> str:
    """Render *seconds* as a short French duration string (e.g. ``1 j 2 h``)."""
    if seconds <= 0:
        return "0 s"
    units = (
        ("j", 86400),
        ("h", 3600),
        ("min", 60),
        ("s", 1),
    )
    parts: list[str] = []
    remaining = seconds
    for label, size in units:
        if remaining >= size:
            qty, remaining = divmod(remaining, size)
            parts.append(f"{qty} {label}")
    return " ".join(parts)
