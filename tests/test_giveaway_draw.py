"""Unit tests for ``features.giveaway.draw.pick_winners``."""

from features.giveaway.draw import pick_winners


def test_returns_empty_for_no_entrants() -> None:
    assert pick_winners([], 3) == []


def test_returns_empty_for_zero_count() -> None:
    assert pick_winners(["a", "b"], 0) == []


def test_picks_requested_number_when_pool_is_larger() -> None:
    pool = [str(i) for i in range(20)]
    winners = pick_winners(pool, 3)
    assert len(winners) == 3
    assert len(set(winners)) == 3
    assert all(w in pool for w in winners)


def test_returns_full_pool_when_count_exceeds_size() -> None:
    pool = ["a", "b", "c"]
    winners = pick_winners(pool, 10)
    assert sorted(winners) == sorted(pool)


def test_drops_duplicates() -> None:
    winners = pick_winners(["a", "a", "b"], 5)
    assert sorted(winners) == ["a", "b"]


def test_excludes_listed_ids() -> None:
    winners = pick_winners(["a", "b", "c"], 5, exclude=["a", "c"])
    assert winners == ["b"]


def test_excluded_pool_drains_correctly() -> None:
    winners = pick_winners(["a"], 1, exclude=["a"])
    assert winners == []


def test_no_repeat_winners_in_single_draw() -> None:
    pool = [str(i) for i in range(50)]
    for _ in range(20):
        winners = pick_winners(pool, 5)
        assert len(set(winners)) == len(winners) == 5
