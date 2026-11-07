"""What a member sees when a command, button or modal fails.

interactions.py's default handlers answer cooldowns and permission checks in
English and say nothing at all for an unexpected exception, so Discord shows
"The application did not respond". ``extensions/errors.py`` replaces them with
these French messages; an unexpected error also carries a short reference that
is logged with the traceback, so a member can report it and an admin can find
it in the logs.
"""

from __future__ import annotations

import secrets

from interactions import errors


def new_reference() -> str:
    """A short id that ties a member's error message to its log line."""
    return secrets.token_hex(3)


def is_expected(error: BaseException) -> bool:
    """Errors that are part of normal use: answered, but not logged as failures."""
    return isinstance(
        error,
        errors.CommandOnCooldown | errors.MaxConcurrencyReached | errors.CommandCheckFailure,
    )


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


__all__ = ["is_expected", "new_reference", "user_message"]
