"""In-memory state that must not grow with every member who ever wrote (B19)."""

from features.moderation.spam import SWEEP_INTERVAL_SECONDS, SpamTracker
from src.core.cache import ExpiringMap


def test_spam_triggers_at_the_threshold_within_the_window():
    tracker = SpamTracker()
    hits = [tracker.hit("a", t, threshold=3, window=5) for t in (0.0, 1.0, 2.0)]
    assert hits == [False, False, True]


def test_spam_starts_over_after_triggering():
    tracker = SpamTracker()
    for t in (0.0, 1.0, 2.0):
        tracker.hit("a", t, threshold=3, window=5)
    assert tracker.hit("a", 2.5, threshold=3, window=5) is False


def test_spam_forgets_messages_older_than_the_window():
    tracker = SpamTracker()
    tracker.hit("a", 0.0, threshold=3, window=5)
    tracker.hit("a", 1.0, threshold=3, window=5)
    assert tracker.hit("a", 10.0, threshold=3, window=5) is False


def test_quiet_members_are_swept_away():
    tracker = SpamTracker()
    for i in range(50):
        tracker.hit(f"member{i}", 0.0, threshold=5, window=7)
    assert len(tracker) == 50

    # One more message after the sweep interval: every quiet window goes.
    tracker.hit("active", SWEEP_INTERVAL_SECONDS + 1, threshold=5, window=7)
    assert len(tracker) == 1


def test_a_window_still_open_survives_the_sweep():
    tracker = SpamTracker()
    tracker.hit("slow", 0.0, threshold=5, window=120)
    tracker.hit("other", SWEEP_INTERVAL_SECONDS + 1, threshold=5, window=7)
    assert len(tracker) == 2


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_expiring_map_returns_a_fresh_value_once():
    clock = Clock()
    pending = ExpiringMap[str](60, clock=clock)
    pending[1] = "choix"
    assert pending.pop(1) == "choix"
    assert pending.pop(1) is None


def test_expiring_map_treats_an_old_value_as_absent():
    clock = Clock()
    pending = ExpiringMap[str](60, clock=clock)
    pending[1] = "choix"
    clock.now = 61
    assert pending.pop(1) is None


def test_expiring_map_drops_abandoned_entries_on_the_next_store():
    clock = Clock()
    pending = ExpiringMap[str](60, clock=clock)
    for member in range(10):
        pending[member] = "abandonné"
    clock.now = 61
    pending[99] = "nouveau"
    assert len(pending) == 1
