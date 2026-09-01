"""Tests for the pure Secret Santa assignment algorithms.

The properties asserted here are the ones the draw promises its users: nobody
draws themselves, everybody gives and receives exactly once, banned pairs are
respected, and — for :func:`generate_valid_assignments` — the chain forms a
*single* loop rather than several disjoint ones.
"""

import random

import pytest

from features.secretsanta.assignments import (
    generate_assignments_with_subgroups,
    generate_valid_assignments,
    is_valid_assignment,
)


def count_cycles(pairs: list[tuple[int, int]]) -> int:
    """Number of disjoint gift-giving loops in a complete assignment."""
    successor = dict(pairs)
    seen: set[int] = set()
    cycles = 0
    for participant in successor:
        if participant in seen:
            continue
        cycles += 1
        current = participant
        while current not in seen:
            seen.add(current)
            current = successor[current]
    return cycles


def assert_well_formed(
    pairs: list[tuple[int, int]],
    participants: list[int],
    banned_pairs: list[tuple[int, int]],
) -> None:
    """Every assignment must be a banned-pair-respecting derangement."""
    givers = [giver for giver, _ in pairs]
    receivers = [receiver for _, receiver in pairs]

    assert sorted(givers) == sorted(participants), "every participant gives exactly once"
    assert sorted(receivers) == sorted(participants), "every participant receives exactly once"
    for giver, receiver in pairs:
        assert giver != receiver, f"{giver} drew themselves"
        assert is_valid_assignment(giver, receiver, banned_pairs), (
            f"{giver} → {receiver} is a banned pair"
        )


# ---------------------------------------------------------------------------
# is_valid_assignment
# ---------------------------------------------------------------------------


def test_bans_are_symmetric():
    banned = [(1, 2)]
    assert not is_valid_assignment(1, 2, banned)
    assert not is_valid_assignment(2, 1, banned)
    assert is_valid_assignment(1, 3, banned)


def test_no_bans_allows_everything():
    assert is_valid_assignment(1, 2, [])


# ---------------------------------------------------------------------------
# generate_valid_assignments — the single-cycle guarantee
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [2, 3, 4, 5, 6, 9, 15])
def test_unbanned_draw_is_always_one_cycle(size):
    """Regression: the search used to return derangements, which may split.

    Before the fix, four participants produced two mutual-swap pairs — e.g.
    ``[(1, 2), (2, 1), (3, 4), (4, 3)]`` — on roughly a third of runs.
    """
    participants = list(range(1, size + 1))
    random.seed(size)
    for _ in range(200):
        pairs = generate_valid_assignments(participants, [])
        assert pairs is not None
        assert_well_formed(pairs, participants, [])
        assert count_cycles(pairs) == 1


def test_draw_respects_bans_and_stays_one_cycle():
    participants = [1, 2, 3, 4, 5, 6]
    banned = [(1, 2), (3, 4)]
    random.seed(0)
    for _ in range(200):
        pairs = generate_valid_assignments(participants, banned)
        assert pairs is not None
        assert_well_formed(pairs, participants, banned)
        assert count_cycles(pairs) == 1


def test_two_participants_swap():
    pairs = generate_valid_assignments([1, 2], [])
    assert pairs is not None
    assert sorted(pairs) == [(1, 2), (2, 1)]
    assert count_cycles(pairs) == 1


def test_fewer_than_two_participants_is_infeasible():
    assert generate_valid_assignments([], []) is None
    assert generate_valid_assignments([1], []) is None


def test_ban_between_the_only_two_participants_is_infeasible():
    assert generate_valid_assignments([1, 2], [(1, 2)]) is None


def test_participant_banned_from_everyone_is_infeasible():
    participants = [1, 2, 3]
    banned = [(1, 2), (1, 3)]
    assert generate_valid_assignments(participants, banned) is None


def test_bans_that_admit_only_subgroups_are_rejected():
    """Two pairs that can only swap internally have no single-cycle solution.

    1 and 2 may only give to each other, as may 3 and 4 — so the only valid
    derangement is two 2-cycles, which single-cycle mode must refuse rather
    than return silently.
    """
    participants = [1, 2, 3, 4]
    banned = [(1, 3), (1, 4), (2, 3), (2, 4)]
    assert generate_valid_assignments(participants, banned) is None


def test_draw_varies_between_runs():
    """Shuffled candidates should not collapse to one fixed answer."""
    participants = [1, 2, 3, 4, 5, 6]
    random.seed(1)
    seen = {tuple(sorted(generate_valid_assignments(participants, []))) for _ in range(60)}
    assert len(seen) > 1


# ---------------------------------------------------------------------------
# generate_assignments_with_subgroups
# ---------------------------------------------------------------------------


def test_subgroup_draw_reports_its_real_cycle_count():
    participants = [1, 2, 3, 4, 5, 6]
    random.seed(2)
    for _ in range(100):
        result = generate_assignments_with_subgroups(participants, [])
        assert result is not None
        pairs, subgroups = result
        assert_well_formed(pairs, participants, [])
        assert count_cycles(pairs) == subgroups


def test_subgroup_draw_solves_what_single_cycle_cannot():
    """The case single-cycle mode refuses is exactly the fallback's job."""
    participants = [1, 2, 3, 4]
    banned = [(1, 3), (1, 4), (2, 3), (2, 4)]
    random.seed(3)
    result = generate_assignments_with_subgroups(participants, banned)
    assert result is not None
    pairs, subgroups = result
    assert_well_formed(pairs, participants, banned)
    assert subgroups == 2
    assert count_cycles(pairs) == 2


def test_subgroup_draw_never_leaves_anyone_in_a_loop_of_one():
    participants = [1, 2, 3, 4, 5]
    random.seed(4)
    for _ in range(100):
        result = generate_assignments_with_subgroups(participants, [])
        assert result is not None
        pairs, _ = result
        successor = dict(pairs)
        for giver in participants:
            assert successor[giver] != giver


def test_subgroup_draw_rejects_infeasible_input():
    assert generate_assignments_with_subgroups([1], []) is None
    assert generate_assignments_with_subgroups([1, 2], [(1, 2)]) is None
    assert generate_assignments_with_subgroups([1, 2, 3], [(1, 2), (1, 3)]) is None


def test_the_retry_path_recovers_when_the_greedy_pass_dead_ends():
    """The greedy chain-builder can strand a participant; the retry must recover.

    With 1 able to give only to 4, this seed drives the first pass into a dead
    end and exercises ``_retry_subgroup_assignment``. Asserted on properties
    rather than the exact pairing, so the test survives a reordering of the
    candidate heuristics.
    """
    participants = [1, 2, 3, 4]
    banned = [(1, 2), (1, 3)]
    random.seed(1)

    result = generate_assignments_with_subgroups(participants, banned)

    assert result is not None
    pairs, subgroups = result
    assert_well_formed(pairs, participants, banned)
    assert count_cycles(pairs) == subgroups
