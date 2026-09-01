"""Pure Secret Santa assignment algorithms (no I/O, no Discord)."""

import os
import random

from src.core.logging import init_logger

logger = init_logger(os.path.basename(__file__))


def is_valid_assignment(giver: int, receiver: int, banned_pairs: list[tuple[int, int]]) -> bool:
    """True iff ``giver → receiver`` is not listed in ``banned_pairs`` (symmetric)."""
    return not any(
        (giver == p1 and receiver == p2) or (giver == p2 and receiver == p1)
        for p1, p2 in banned_pairs
    )


def _build_valid_receivers(
    participant_ids: list[int], banned_pairs: list[tuple[int, int]]
) -> dict[int, list[int]]:
    return {
        giver: [
            receiver
            for receiver in participant_ids
            if receiver != giver and is_valid_assignment(giver, receiver, banned_pairs)
        ]
        for giver in participant_ids
    }


class _SearchBudget:
    """Bounds a backtracking search so a pathological ban list can't hang the caller.

    Finding a Hamiltonian cycle is NP-hard in general, and the draw runs inline
    in a slash-command handler. Exhausting the budget is reported as "no
    assignment found", which the caller already handles.
    """

    def __init__(self, limit: int) -> None:
        self.remaining = limit

    def spend(self) -> bool:
        """Consume one node expansion; ``False`` once the budget is spent."""
        self.remaining -= 1
        return self.remaining > 0


# Generous for the group sizes a Discord Secret Santa sees (a full search over
# 20 participants with no bans settles in far fewer steps), low enough that an
# unsatisfiable ban list gives up in well under a second.
_MAX_SEARCH_STEPS = 200_000


def _backtrack_cycle(
    start: int,
    current: int,
    unvisited: set[int],
    valid_receivers: dict[int, list[int]],
    assignments: dict[int, int],
    budget: _SearchBudget,
) -> bool:
    """Extend the single gift-giving cycle from *current*, backtracking on dead ends.

    The cycle is grown one participant at a time: every step consumes a member
    of *unvisited*, and the final step must close the loop back to *start*.
    That last check is what makes the result one cycle rather than merely a
    derangement — a derangement is free to split into several disjoint loops.
    """
    if not unvisited:
        # Everyone is on the path; the assignment is only valid if the last
        # giver may give to the participant the cycle started from.
        if start in valid_receivers[current]:
            assignments[current] = start
            return True
        return False

    if not budget.spend():
        return False

    candidates = [r for r in valid_receivers[current] if r in unvisited]
    random.shuffle(candidates)
    # Warnsdorff-style ordering: visit the most constrained successor first so
    # dead ends surface early instead of after a deep fruitless descent.
    candidates.sort(key=lambda c: sum(1 for r in valid_receivers[c] if r in unvisited))

    for receiver in candidates:
        assignments[current] = receiver
        unvisited.remove(receiver)

        if _backtrack_cycle(start, receiver, unvisited, valid_receivers, assignments, budget):
            return True

        unvisited.add(receiver)
        del assignments[current]

    return False


def generate_valid_assignments(
    participant_ids: list[int], banned_pairs: list[tuple[int, int]]
) -> list[tuple[int, int]] | None:
    """Generate a single-cycle Secret Santa assignment using smart backtracking.

    Every participant gives to exactly one other and receives from exactly one
    other, and following the chain visits everybody before returning to the
    start — so the draw can never degenerate into two people swapping gifts
    with each other while the rest form their own loop.

    Returns ``None`` when no valid single-cycle assignment exists (or when the
    search budget runs out); callers that can live with several independent
    loops fall back to :func:`generate_assignments_with_subgroups`.
    """
    if len(participant_ids) < 2:
        return None

    valid_receivers = _build_valid_receivers(participant_ids, banned_pairs)

    for giver, receivers in valid_receivers.items():
        if not receivers:
            logger.warning(f"No valid receivers for participant {giver}")
            return None

    # The cycle is a loop, so its starting point is arbitrary — candidate
    # shuffling inside the search is what varies the draw between runs.
    start = participant_ids[0]
    assignments: dict[int, int] = {}
    unvisited = set(participant_ids) - {start}
    budget = _SearchBudget(_MAX_SEARCH_STEPS)

    if _backtrack_cycle(start, start, unvisited, valid_receivers, assignments, budget):
        return [(giver, assignments[giver]) for giver in participant_ids]

    if budget.remaining <= 0:
        logger.warning(
            "Single-cycle search gave up after %d steps for %d participants",
            _MAX_SEARCH_STEPS,
            len(participant_ids),
        )
    return None


def _retry_subgroup_assignment(
    participant_ids: list[int],
    banned_pairs: list[tuple[int, int]],
    max_retries: int = 50,
) -> tuple[list[tuple[int, int]], int] | None:
    """Retry subgroup assignment with different random starts."""
    valid_receivers = _build_valid_receivers(participant_ids, banned_pairs)

    for _ in range(max_retries):
        assignments: dict[int, int] = {}
        remaining = set(participant_ids)
        subgroups = 0
        success = True

        while remaining and success:
            participants_list = list(remaining)
            random.shuffle(participants_list)
            subgroup_start = participants_list[0]
            current = subgroup_start
            subgroup_members = [current]
            remaining.remove(current)

            while True:
                candidates = [r for r in valid_receivers[current] if r in remaining]

                if not candidates:
                    if len(subgroup_members) >= 2 and is_valid_assignment(
                        current, subgroup_start, banned_pairs
                    ):
                        assignments[current] = subgroup_start
                        subgroups += 1
                        break
                    success = False
                    break

                random.shuffle(candidates)
                next_person = candidates[0]
                assignments[current] = next_person
                subgroup_members.append(next_person)
                remaining.remove(next_person)
                current = next_person

        if success and len(assignments) == len(participant_ids):
            return [(giver, assignments[giver]) for giver in participant_ids], subgroups

    return None


def generate_assignments_with_subgroups(
    participant_ids: list[int], banned_pairs: list[tuple[int, int]]
) -> tuple[list[tuple[int, int]], int] | None:
    """Generate assignments that may fall into several independent cycles.

    Returns ``(assignments, number_of_subgroups)`` or ``None`` if infeasible.
    Each subgroup forms its own gift-giving cycle.
    """
    if len(participant_ids) < 2:
        return None

    valid_receivers = _build_valid_receivers(participant_ids, banned_pairs)

    for giver, receivers in valid_receivers.items():
        if not receivers:
            logger.warning(f"No valid receivers for participant {giver}")
            return None

    assignments: dict[int, int] = {}
    remaining = set(participant_ids)
    subgroups = 0

    while remaining:
        subgroup_start = random.choice(list(remaining))
        current = subgroup_start
        subgroup_members = [current]
        remaining.remove(current)

        while True:
            candidates = [r for r in valid_receivers[current] if r in remaining]

            if not candidates:
                if len(subgroup_members) >= 2 and is_valid_assignment(
                    current, subgroup_start, banned_pairs
                ):
                    assignments[current] = subgroup_start
                    subgroups += 1
                    break
                return _retry_subgroup_assignment(participant_ids, banned_pairs)

            random.shuffle(candidates)
            candidates.sort(
                key=lambda c: len(
                    [r for r in valid_receivers[c] if r in remaining or r == subgroup_start]
                )
            )

            next_person = candidates[0]
            assignments[current] = next_person
            subgroup_members.append(next_person)
            remaining.remove(next_person)
            current = next_person

    if len(assignments) != len(participant_ids):
        return None

    return [(giver, assignments[giver]) for giver in participant_ids], subgroups
