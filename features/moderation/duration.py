"""Duration bounds and formatting for moderation timeouts (parsing: ``src.core.text``).

Pure helpers — no Discord or DB imports — so they are trivially unit-testable.
"""

# Discord caps member timeouts at 28 days.
MAX_TIMEOUT_SECONDS = 28 * 86400


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
