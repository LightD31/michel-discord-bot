"""Embed primitives: the spacer field.

Colours, embeds and footers come from :mod:`src.discord_ext.look` (P6, B8).
The Discord timestamp formatter is ``src.core.text.format_discord_timestamp``
(pure, so ``features/`` can use it too).
"""

# Zero-width space field — useful to force an empty cell in a 3-column embed.
SPACER_FIELD = {"name": "​", "value": "​", "inline": True}


__all__ = ["SPACER_FIELD"]
