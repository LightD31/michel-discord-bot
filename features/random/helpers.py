"""Pure business-logic helpers for the random feature (no Discord, no DB)."""

MAX_CHOICES = 100
MAX_DIE_FACES = 1_000_000
MIN_DIE_FACES = 2

_ERROR_MESSAGES = {
    "fr": {
        "no_choice": "🤔 Compliqué de faire un choix quand il n'y a pas le choix ! Ajoutez au moins 2 options.",
        "too_many_choices": f"😅 Trop de choix ! Limitez-vous à {MAX_CHOICES} options maximum.",
        "invalid_die_faces": f"🎲 Un dé doit avoir entre {MIN_DIE_FACES} et {MAX_DIE_FACES:,} faces !",
    },
    "en": {
        "no_choice": "🤔 Hard to choose when there is no choice! Give at least 2 options.",
        "too_many_choices": f"😅 Too many choices! {MAX_CHOICES} options at most.",
        "invalid_die_faces": f"🎲 A die has between {MIN_DIE_FACES} and {MAX_DIE_FACES:,} faces!",
    },
}


def _messages(french: bool) -> dict[str, str]:
    return _ERROR_MESSAGES["fr" if french else "en"]


def validate_choices(choices: list, *, french: bool = True) -> str | None:
    """Return an error message string if *choices* is invalid, else None."""
    if len(choices) <= 1:
        return _messages(french)["no_choice"]
    if len(choices) > MAX_CHOICES:
        return _messages(french)["too_many_choices"]
    return None


def validate_die_faces(faces: int, *, french: bool = True) -> str | None:
    """Return an error message string if *faces* is out of range, else None."""
    if faces < MIN_DIE_FACES or faces > MAX_DIE_FACES:
        return _messages(french)["invalid_die_faces"]
    return None
