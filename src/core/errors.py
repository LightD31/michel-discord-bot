"""Base exception hierarchy for the Michel Discord Bot.

Every exception raised by internal code should ultimately derive from
:class:`BotError` so callers can catch-all without catching the world. Future
PRs will migrate existing feature-specific exceptions (``BirthdayError``,
``DatabaseError`` inside birthdayext, ``ZuniversAPIError`` in ``src/coloc``,
``ConfrerieError`` in confrerieext, …) onto this hierarchy.

For Phase 1 this module only *defines* the base classes — no existing code is
rewritten, so nothing breaks.

Tree
----
- :class:`BotError`                       — root
    - :class:`ConfigError`                — ``config/config.json`` problems
    - :class:`DatabaseError`              — MongoDB operation failed unrecoverably
    - :class:`IntegrationError`           — third-party integration failed
        - :class:`HttpError`              — HTTP request failed after retries
    - :class:`ValidationError`            — user-supplied input is invalid
"""


class BotError(Exception):
    """Root of the bot's exception hierarchy."""


class ConfigError(BotError):
    """Raised when the configuration is missing or malformed."""


class DatabaseError(BotError):
    """Raised when a MongoDB operation fails unrecoverably."""


class ValidationError(BotError):
    """Raised when user-supplied input fails validation."""


class IntegrationError(BotError):
    """Base class for third-party integration failures.

    Concrete integrations (Spotify, Twitch, Notion, Raider.IO, …) may subclass
    this to signal an error originating from an external API while still being
    caught by ``except IntegrationError``.
    """


class HttpError(IntegrationError):
    """Raised by :func:`src.core.http.fetch` when an HTTP request ultimately fails.

    The final status code (if any) and URL are preserved so callers can log
    without losing context.
    """

    def __init__(self, message: str, *, url: str, status: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status = status

    def __str__(self) -> str:
        if self.status is not None:
            return f"{super().__str__()} (url={self.url!r}, status={self.status})"
        return f"{super().__str__()} (url={self.url!r})"


__all__ = [
    "BotError",
    "ConfigError",
    "DatabaseError",
    "HttpError",
    "IntegrationError",
    "ValidationError",
]
