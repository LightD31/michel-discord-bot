"""Base exception hierarchy for the Michel Discord Bot.

Every exception raised by internal code derives from :class:`BotError`, so
callers can catch the bot's own failures without catching the world.
Repositories raise :class:`DatabaseError` (never a driver exception), clients
of third-party APIs an :class:`IntegrationError`.

Tree
----
- :class:`BotError`                       — root
    - :class:`ConfigError`                — ``config/config.json`` problems
    - :class:`DatabaseError`              — MongoDB operation failed unrecoverably
    - :class:`IntegrationError`           — third-party integration failed
        - :class:`HttpError`              — HTTP request failed after retries
    - :class:`ValidationError`            — user-supplied input is invalid
"""

from collections.abc import Callable

from aiohttp import ClientError

from src.core.urls import redact_url, redact_urls_in


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
    without losing context. Both the URL and every URL inside the message are
    redacted on construction (:mod:`src.core.urls`): the error ends up in log
    lines and stored error fields, and must never carry a credential that was
    passed as a query parameter or userinfo.
    """

    def __init__(self, message: str, *, url: str, status: int | None = None) -> None:
        super().__init__(redact_urls_in(message))
        self.url = redact_url(url)
        self.status = status

    def __str__(self) -> str:
        if self.status is not None:
            return f"{super().__str__()} (url={self.url!r}, status={self.status})"
        return f"{super().__str__()} (url={self.url!r})"


# Failures that come from outside the code (an outage, a refusal) rather than
# from a bug in it: a caught one is logged on one line, without its traceback.
EXPECTED_FAILURES: tuple[type[BaseException], ...] = (
    DatabaseError,
    IntegrationError,
    TimeoutError,
    ConnectionError,
    ClientError,
)

_expected_checks: list[Callable[[BaseException], bool]] = []


def register_expected_failure(check: Callable[[BaseException], bool]) -> None:
    """Also count as expected the errors *check* accepts (a library's own outage errors)."""
    if check not in _expected_checks:
        _expected_checks.append(check)


def is_expected_failure(error: BaseException) -> bool:
    """Whether *error* is an outage or a refusal rather than a bug.

    The bot's own database and integration errors, timeouts and connection
    failures, plus what :func:`register_expected_failure` added (Discord's
    403/404/5xx answers, Spotify's transport errors).
    """
    if isinstance(error, EXPECTED_FAILURES):
        return True
    return any(check(error) for check in _expected_checks)


__all__ = [
    "EXPECTED_FAILURES",
    "BotError",
    "ConfigError",
    "DatabaseError",
    "HttpError",
    "IntegrationError",
    "ValidationError",
    "is_expected_failure",
    "register_expected_failure",
]
