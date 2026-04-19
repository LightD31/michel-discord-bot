"""Pure (Discord-agnostic) helpers for poll logic."""

MAX_POLL_OPTIONS = 10


def validate_poll_options(options: list[str]) -> bool:
    """True when the option count fits within the emoji pool."""
    return len(options) <= MAX_POLL_OPTIONS


def parse_poll_author_id(footer_text: str) -> str | None:
    """Extract the author ID from a poll embed footer like
    ``"Créé par <name> (ID: <id>)"``."""
    if not footer_text or len(footer_text.split(" ")) < 5:
        return None
    return footer_text.split(" ")[4].rstrip(")")


__all__ = ["MAX_POLL_OPTIONS", "parse_poll_author_id", "validate_poll_options"]
