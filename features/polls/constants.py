"""Constants for the polls feature — emoji sets and default options."""

POLL_EMOJIS: list[str] = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# A public poll whose options are left empty.
DEFAULT_POLL_OPTIONS: list[str] = ["Oui", "Non"]

__all__ = ["DEFAULT_POLL_OPTIONS", "POLL_EMOJIS"]
