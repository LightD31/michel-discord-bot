"""Tests for the pure poll tally helpers (first-past-the-post and IRV).

Pins down the judgment calls the instant-runoff implementation makes:
which round declares a majority, how ties break, what happens to ballots
whose every choice has been eliminated, and how malformed rankings are
filtered.
"""

import pytest

from features.polls.tally import (
    parse_duration,
    render_bar,
    tally_first_past_post,
    tally_ranked_choice,
)

# ---------------------------------------------------------------------------
# First past the post
# ---------------------------------------------------------------------------


def test_only_the_first_preference_counts():
    counts = tally_first_past_post({"a": [0, 1], "b": [0, 2], "c": [1, 0]}, num_options=3)
    assert counts == [2, 1, 0]


def test_out_of_range_and_empty_ballots_are_ignored():
    counts = tally_first_past_post({"a": [5], "b": [-1], "c": [], "d": [1]}, num_options=3)
    assert counts == [0, 1, 0]


def test_no_votes_gives_all_zeroes():
    assert tally_first_past_post({}, num_options=3) == [0, 0, 0]


# ---------------------------------------------------------------------------
# Instant-runoff — degenerate inputs
# ---------------------------------------------------------------------------


def test_no_votes_has_no_winner():
    assert tally_ranked_choice({}, 3) == ([], None)


def test_no_options_has_no_winner():
    assert tally_ranked_choice({"a": [0]}, 0) == ([], None)


def test_ballots_with_only_invalid_choices_have_no_winner():
    assert tally_ranked_choice({"a": [7, -2], "b": [9]}, 3) == ([], None)


# ---------------------------------------------------------------------------
# Instant-runoff — the counting rules
# ---------------------------------------------------------------------------


def test_first_round_majority_ends_the_count():
    rounds, winner = tally_ranked_choice(
        {"a": [0, 1], "b": [0, 2], "c": [1, 0], "d": [1, 0], "e": [0, 1]}, 3
    )
    assert winner == 0
    assert len(rounds) == 1, "a majority in round one must not trigger an elimination"
    assert rounds[0] == [3, 2, 0]


def test_runoff_redistributes_the_eliminated_option():
    """2-2-1 → option 2 is eliminated, its ballot moves to option 0."""
    rounds, winner = tally_ranked_choice(
        {"a": [0, 2], "b": [0, 2], "c": [1, 2], "d": [1, 2], "e": [2, 0]}, 3
    )
    assert winner == 0
    assert rounds[0] == [2, 2, 1]
    assert rounds[-1] == [3, 2, 0]


def test_eliminated_options_report_zero_in_later_rounds():
    rounds, _ = tally_ranked_choice(
        {"a": [0, 2], "b": [0, 2], "c": [1, 2], "d": [1, 2], "e": [2, 0]}, 3
    )
    assert rounds[-1][2] == 0


def test_duplicate_choices_within_a_ballot_count_once():
    rounds, winner = tally_ranked_choice({"a": [0, 0, 1], "b": [1]}, 2)
    assert rounds[0] == [1, 1]
    # A perfect tie resolves to the earliest option still standing.
    assert winner == 0


def test_perfect_tie_resolves_to_the_earliest_option():
    rounds, winner = tally_ranked_choice({"a": [0], "b": [1], "c": [2]}, 3)
    assert rounds[0] == [1, 1, 1]
    assert winner == 0


def test_exhausted_ballots_drop_out_of_the_majority_base():
    """b and c rank only option 1; once it is out their ballots are exhausted.

    Option 1 leads 2-1 but holds no majority, so the lowest (option 0) would
    normally go — here the tie-break keeps the count moving and option 1 wins
    on the remaining active ballots.
    """
    rounds, winner = tally_ranked_choice({"a": [0], "b": [1], "c": [1], "d": [2]}, 3)
    assert rounds[0] == [1, 2, 1]
    assert winner == 1


def test_single_candidate_wins_immediately():
    rounds, winner = tally_ranked_choice({"a": [0], "b": [0]}, 1)
    assert winner == 0
    assert rounds == [[2]]


def test_invalid_indices_are_stripped_before_counting():
    rounds, winner = tally_ranked_choice({"a": [9, 0], "b": [0, 4]}, 2)
    assert rounds[0] == [2, 0]
    assert winner == 0


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "total", "expected_filled"),
    [(0, 10, 0), (10, 10, 12), (5, 10, 6), (1, 10, 1)],
)
def test_bar_fills_proportionally(count, total, expected_filled):
    bar = render_bar(count, total, length=12)
    assert len(bar) == 12
    assert bar.count("▰") == expected_filled


def test_bar_is_empty_when_nobody_voted():
    assert render_bar(0, 0) == "▱" * 12


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30m", 1800),
        ("2h", 7200),
        ("1d", 86400),
        ("45s", 45),
        ("90", 5400),
        ("1h30m", 5400),
        (" 2H ", 7200),
    ],
)
def test_durations_parse(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "bad", "m", "1x", "0m", "0"])
def test_malformed_durations_are_rejected(text):
    assert parse_duration(text) is None
