"""Reusable UI helpers that depend on ``interactions`` (discord-py-interactions).

Modules
-------
- :mod:`src.discord_ext.embeds`       — color palette, spacer field, timestamp formatter
- :mod:`src.discord_ext.messages`     — send_error/success, require_guild, fetch_user_safe,
                                        persistent-message bootstrapping, thread unarchive
- :mod:`src.discord_ext.autocomplete` — shared autocomplete handlers + guild-enabled check
- :mod:`src.discord_ext.paginator`    — CustomPaginator + reaction-poll formatter

These are the helpers every extension imports. Anything that would *still* make
sense in a non-Discord context (text processing, image rendering, HTTP) lives
under :mod:`src.core` instead.

Old paths (``src.helpers``, ``src.utils``) are kept as re-export shims for one
release so callers don't have to update in a single go.
"""
