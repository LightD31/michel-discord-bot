"""Pure tally helpers — first-past-the-post and instant-runoff voting."""


def tally_first_past_post(
    votes: dict[str, list[int]], num_options: int
) -> list[int]:
    """Return per-option vote counts. ``votes`` values use only their first index."""
    counts = [0] * num_options
    for ranking in votes.values():
        if ranking and 0 <= ranking[0] < num_options:
            counts[ranking[0]] += 1
    return counts


def tally_ranked_choice(
    votes: dict[str, list[int]], num_options: int
) -> tuple[list[list[int]], int | None]:
    """Run instant-runoff voting (IRV).

    Returns ``(rounds, winner)`` where ``rounds`` is a list of per-round vote
    counts (one entry per option, ``0`` for eliminated options) and ``winner``
    is the index of the winning option (``None`` if no votes were cast).

    Tie-breaking on elimination: drops the lowest-indexed option among the
    smallest. Stops early if a candidate holds a strict majority of remaining
    ballots.
    """
    if not votes or num_options <= 0:
        return [], None

    # Filter rankings to valid indices, dedup within each ballot.
    ballots: list[list[int]] = []
    for ranking in votes.values():
        seen: set[int] = set()
        cleaned = []
        for idx in ranking:
            if 0 <= idx < num_options and idx not in seen:
                seen.add(idx)
                cleaned.append(idx)
        if cleaned:
            ballots.append(cleaned)

    if not ballots:
        return [], None

    eliminated: set[int] = set()
    rounds: list[list[int]] = []

    while True:
        counts = [0] * num_options
        for ballot in ballots:
            for choice in ballot:
                if choice not in eliminated:
                    counts[choice] += 1
                    break
        rounds.append(counts.copy())

        active_total = sum(counts)
        if active_total == 0:
            return rounds, None

        # Majority winner?
        max_count = max(counts)
        if max_count * 2 > active_total:
            return rounds, counts.index(max_count)

        # Eliminate the lowest non-zero candidate(s); if all remaining are tied,
        # return the first-indexed remaining option.
        active_counts = [
            (i, c) for i, c in enumerate(counts) if i not in eliminated and c > 0
        ]
        if not active_counts:
            return rounds, None
        if len({c for _, c in active_counts}) == 1:
            # Perfect tie — return earliest option still standing.
            return rounds, active_counts[0][0]

        min_count = min(c for _, c in active_counts)
        for i, c in active_counts:
            if c == min_count:
                eliminated.add(i)
                break  # eliminate one per round to stay deterministic


def render_bar(count: int, total: int, length: int = 12) -> str:
    """Render a unicode progress bar of ``length`` cells based on ``count/total``."""
    if total <= 0:
        return "▱" * length
    filled = round(count / total * length)
    filled = max(0, min(filled, length))
    return "▰" * filled + "▱" * (length - filled)


def parse_duration(text: str) -> int | None:
    """Parse a duration string like ``"30m"``, ``"2h"``, ``"1d"``, ``"45"``.

    Returns total seconds or ``None`` on malformed input. Bare numbers are
    minutes for backwards-friendliness with the slash-command UX.
    """
    if not text:
        return None
    text = text.strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    total = 0
    current = ""
    for ch in text:
        if ch.isdigit():
            current += ch
            continue
        if ch in multipliers and current:
            total += int(current) * multipliers[ch]
            current = ""
            continue
        return None
    if current:
        total += int(current) * 60  # bare number → minutes
    return total if total > 0 else None


__all__ = [
    "parse_duration",
    "render_bar",
    "tally_first_past_post",
    "tally_ranked_choice",
]


# Light sanity tests so behavior is documented in source.
if __name__ == "__main__":
    # Plurality: option 0 wins
    counts = tally_first_past_post(
        {"a": [0], "b": [0], "c": [1]}, num_options=3
    )
    assert counts == [2, 1, 0], counts

    # Ranked: A wins outright with majority of first-choice votes.
    rounds, winner = tally_ranked_choice(
        {"a": [0, 1], "b": [0, 2], "c": [1, 0], "d": [1, 0], "e": [0, 1]}, 3
    )
    assert winner == 0, (rounds, winner)

    # Ranked: requires runoff. 2-2-1 -> eliminate 2 -> redistribute.
    rounds, winner = tally_ranked_choice(
        {
            "a": [0, 2],
            "b": [0, 2],
            "c": [1, 2],
            "d": [1, 2],
            "e": [2, 0],
        },
        3,
    )
    # After eliminating option 2 (count 1), e's vote moves to 0 → 0 wins 3-2.
    assert winner == 0, (rounds, winner)

    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration("1d") == 86400
    assert parse_duration("90") == 5400
    assert parse_duration("bad") is None
    print("polls.tally self-tests passed")
