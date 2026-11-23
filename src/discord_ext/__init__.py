"""Reusable UI helpers that depend on ``interactions`` (discord-py-interactions).

Among them: :mod:`~src.discord_ext.look` (every card, embed or Components V2,
and the replies' icons), :mod:`~src.discord_ext.messages` (refusals, the
context helpers, persistent messages, change-aware edits),
:mod:`~src.discord_ext.channels` (a guild's channel lookups) and
:mod:`~src.discord_ext.paginator`.

These are the helpers every extension imports. Anything that would *still* make
sense in a non-Discord context (text processing, image rendering, HTTP) lives
under :mod:`src.core` instead.
"""
