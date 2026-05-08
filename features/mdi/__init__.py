"""Raider.IO MDI client and domain models (pure, no Discord dependency)."""

from features.mdi.client import (
    BracketInfo,
    GameSnapshot,
    MatchSnapshot,
    TeamRef,
    get_bracket_matches,
    invalidate_cache,
    list_brackets,
)

__all__ = [
    "BracketInfo",
    "GameSnapshot",
    "MatchSnapshot",
    "TeamRef",
    "get_bracket_matches",
    "invalidate_cache",
    "list_brackets",
]
