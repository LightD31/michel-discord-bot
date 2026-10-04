"""Planning the removal of duplicate tracks from a Spotify playlist."""

from features.spotify.duplicates import plan_duplicate_removal


def apply(track_ids, plan):
    """Replay a plan the way the Spotify API applies it."""
    ids = list(track_ids)
    for track_id, position in plan:
        ids = [i for i in ids if i != track_id]
        ids.insert(position, track_id)
    return ids


def test_no_duplicates_no_plan():
    assert plan_duplicate_removal(["a", "b", "c"]) == []


def test_single_duplicate_keeps_first_position():
    ids = ["a", "b", "a", "c"]
    plan = plan_duplicate_removal(ids)
    assert plan == [("a", 0)]
    assert apply(ids, plan) == ["a", "b", "c"]


def test_positions_account_for_earlier_operations():
    ids = ["x", "a", "a", "b", "a", "b", "c"]
    plan = plan_duplicate_removal(ids)
    assert plan == [("a", 1), ("b", 2)]
    assert apply(ids, plan) == ["x", "a", "b", "c"]


def test_triplicate_collapses_to_one():
    ids = ["c", "a", "c", "c"]
    assert apply(ids, plan_duplicate_removal(ids)) == ["c", "a"]


def test_tracks_without_id_are_left_alone_but_count_for_positions():
    ids = [None, None, "a", "b", "a"]
    plan = plan_duplicate_removal(ids)
    assert plan == [("a", 2)]
    assert apply(ids, plan) == [None, None, "a", "b"]
