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
from features.mdi.repository import MdiMatchesRepository

__all__ = [
    "BracketInfo",
    "GameSnapshot",
    "MatchSnapshot",
    "MdiMatchesRepository",
    "TeamRef",
    "get_bracket_matches",
    "invalidate_cache",
    "list_brackets",
]
