"""Unit tests for ``src.core.text.take_within_budget``."""

from src.core.text import take_within_budget


def test_everything_fits_when_the_budget_is_ample() -> None:
    entries = ["alice", "bob", "carol"]
    kept, used = take_within_budget(entries, 1000)
    assert kept == entries
    assert used == len(", ".join(entries))


def test_separators_are_counted_so_the_join_really_fits() -> None:
    """Counting only the entries would overrun once joined."""
    entries = ["aaaa", "bbbb", "cccc"]  # 12 chars of text, 16 once joined
    kept, used = take_within_budget(entries, 14)
    assert kept == ["aaaa", "bbbb"]
    assert used == len(", ".join(kept)) == 10


def test_order_is_preserved_so_the_caller_controls_what_is_dropped() -> None:
    entries = ["keep_me", "and_me", "drop_me"]
    kept, _ = take_within_budget(entries, len("keep_me, and_me"))
    assert kept == ["keep_me", "and_me"]


def test_a_budget_too_small_for_even_one_entry_keeps_nothing() -> None:
    kept, used = take_within_budget(["a_long_name"], 3)
    assert kept == []
    assert used == 0


def test_empty_input_and_zero_budget() -> None:
    assert take_within_budget([], 100) == ([], 0)
    assert take_within_budget(["a"], 0) == ([], 0)


def test_a_custom_separator_is_accounted_for() -> None:
    kept, used = take_within_budget(["ab", "cd"], 5, separator=" | ")
    assert kept == ["ab"]
    assert used == 2
