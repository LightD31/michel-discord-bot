"""What a member sees when a command, button or modal fails.

interactions.py's default handlers answer cooldowns and permission checks in
English and say nothing at all for an unexpected exception, so Discord shows
"The application did not respond". ``extensions/errors.py`` replaces them with
these French messages; an unexpected error also carries a short reference that
is logged with the traceback, so a member can report it and an admin can find
it in the logs.
"""

from __future__ import annotations

import logging

from interactions import errors

from src.core import log_context
from src.core.errors import register_expected_failure


def new_reference() -> str:
    """The id that ties a member's error message to the log lines of what failed.

    The interaction's correlation id when one was bound
    (``src/discord_ext/interaction_context.py``), so the reference finds every
    line the interaction logged, not only the error.
    """
    return log_context.correlation_id() or log_context.new_id()


def is_expected(error: BaseException) -> bool:
    """Errors that are part of normal use: answered, but not logged as failures."""
    return isinstance(
        error,
        errors.CommandOnCooldown | errors.MaxConcurrencyReached | errors.CommandCheckFailure,
    )


def is_discord_refusal_or_outage(error: BaseException) -> bool:
    """A refusal (403, 404), a rate limit or an outage (5xx) from Discord's API.

    Not a bug in the bot: a missing permission or a deleted target is for a
    server's admins to fix. A 400 means the bot sent something malformed.
    """
    if not isinstance(error, errors.HTTPException):
        return False
    status = getattr(error, "status", 0)
    return status in (403, 404, 429) or status >= 500


def install() -> None:
    """Have :func:`src.core.logging.log_failure` log Discord's refusals and outages on one line."""
    register_expected_failure(is_discord_refusal_or_outage)


def user_message(error: BaseException, reference: str) -> str:
    """The ephemeral reply for *error*."""
    if isinstance(error, errors.CommandOnCooldown):
        seconds = max(int(error.cooldown.get_cooldown_time()), 1)
        return f"⏳ Doucement ! Réessaie dans {seconds} s."
    if isinstance(error, errors.MaxConcurrencyReached):
        return "⏳ Cette commande est déjà en cours, réessaie dans un instant."
    if isinstance(error, errors.CommandCheckFailure):
        return "🚫 Tu n'as pas la permission d'utiliser cette commande."
    return (
        "❌ Une erreur inattendue est survenue. Réessaie plus tard ; si ça continue, "
        f"donne cette référence à un admin : `{reference}`."
    )


def failure_reply(what: str, error: BaseException, logger: logging.Logger) -> str:
    """Log why *what* failed and say it to the member, without the exception's own text.

    *what* reads as a sentence on its own ("Impossible de bannir ce membre").
    The library's message (a route, a JSON error body) means nothing to a
    member and can end up in a public channel. A refusal from Discord or a
    target that is gone is a normal outcome, logged as a warning; anything
    else is logged with its traceback, under the reference the member is given.
    """
    if isinstance(error, errors.Forbidden):
        logger.warning("%s: %s", what, error)
        return f"{what} : je n'ai pas les droits nécessaires (permission ou position de mon rôle)."
    if isinstance(error, errors.NotFound):
        logger.warning("%s: %s", what, error)
        return f"{what} : introuvable sur Discord."
    reference = new_reference()
    logger.error("%s (réf. %s)", what, reference, exc_info=error)
    return f"{what}. Si ça continue, donne cette référence à un admin : `{reference}`."


__all__ = [
    "failure_reply",
    "install",
    "is_discord_refusal_or_outage",
    "is_expected",
    "new_reference",
    "user_message",
]
