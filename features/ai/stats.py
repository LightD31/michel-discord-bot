"""Model leaderboard from stored comparisons — pure, so it tests without MongoDB."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from features.ai.models import Comparison
from features.ai.render import tally, winners


@dataclass
class ModelStats:
    key: str
    display_name: str
    votes: int = 0
    appearances: int = 0
    wins: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.appearances if self.appearances else 0.0


def compute_model_stats(comparisons: Iterable[Comparison]) -> list[ModelStats]:
    """Votes, appearances and wins per model key, best first.

    Only comparisons that received at least one vote count — an ignored
    comparison says nothing about the models in it. Ties count as a win for
    every tied model. The display name is the most recent one seen.
    """
    stats: dict[str, ModelStats] = {}
    for comparison in comparisons:
        keys = [a.key for a in comparison.answers]
        counts = tally(keys, comparison.votes)
        if sum(counts) == 0:
            continue
        best = winners(counts)
        for i, answer in enumerate(comparison.answers):
            entry = stats.setdefault(answer.key, ModelStats(answer.key, answer.display_name))
            entry.display_name = answer.display_name or entry.display_name
            entry.appearances += 1
            entry.votes += counts[i]
            if i in best:
                entry.wins += 1
    return sorted(stats.values(), key=lambda s: (-s.win_rate, -s.votes, s.display_name))
