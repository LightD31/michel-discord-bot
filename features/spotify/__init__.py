"""Spotify feature — MongoDB persistence for playlists, votes, and reminders."""

from features.spotify.cooldown import VoteCooldown
from features.spotify.duplicates import plan_duplicate_removal
from features.spotify.repository import SpotifyRepository
from features.spotify.stats import PlaylistStats, aggregate
from features.spotify.vote_reminder import MIN_VOTES, should_send_low_vote_reminder

__all__ = [
    "MIN_VOTES",
    "PlaylistStats",
    "SpotifyRepository",
    "VoteCooldown",
    "aggregate",
    "plan_duplicate_removal",
    "should_send_low_vote_reminder",
]
