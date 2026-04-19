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


def _has_valid_future(
    givers: list[int],
    start_index: int,
    available_receivers: set[int],
    valid_receivers: dict[int, list[int]],
) -> bool:
    """Forward checking: verify all future givers still have at least one candidate."""
    for i in range(start_index, len(givers)):
        giver = givers[i]
        if not any(r in available_receivers for r in valid_receivers[giver]):
            return False
    return True


def _backtrack_assign(
    givers: list[int],
    index: int,
    assignments: dict[int, int],
    available_receivers: set[int],
    valid_receivers: dict[int, list[int]],
) -> bool:
    if index == len(givers):
        return True

    giver = givers[index]
    candidates = [r for r in valid_receivers[giver] if r in available_receivers]
    random.shuffle(candidates)

    for receiver in candidates:
        assignments[giver] = receiver
        available_receivers.remove(receiver)

        if _has_valid_future(
            givers, index + 1, available_receivers, valid_receivers
        ) and _backtrack_assign(
            givers, index + 1, assignments, available_receivers, valid_receivers
        ):
            return True

        available_receivers.add(receiver)
        del assignments[giver]

    return False


def generate_valid_assignments(
    participant_ids: list[int], banned_pairs: list[tuple[int, int]]
) -> list[tuple[int, int]] | None:
    """Generate a single-cycle Secret Santa assignment using smart backtracking.

    Uses constraint propagation, the MRV (Minimum Remaining Values) heuristic,
    forward checking, and shuffled candidate ordering for variety. Returns
    ``None`` when no valid single-cycle assignment exists.
    """
    if len(participant_ids) < 2:
        return None

    valid_receivers = _build_valid_receivers(participant_ids, banned_pairs)

    for giver, receivers in valid_receivers.items():
        if not receivers:
            logger.warning(f"No valid receivers for participant {giver}")
            return None

    givers = participant_ids.copy()
    random.shuffle(givers)
    givers.sort(key=lambda g: len(valid_receivers[g]))

    assignments: dict[int, int] = {}
    available_receivers = set(participant_ids)

    if _backtrack_assign(givers, 0, assignments, available_receivers, valid_receivers):
        return [(giver, assignments[giver]) for giver in participant_ids]

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
