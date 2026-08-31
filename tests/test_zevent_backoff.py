"""Unit tests for ``features.zevent.backoff``.

The behaviour under test is the one that matters for a community-run API:
failing must never make the client retry *faster* than it succeeds.
"""

from datetime import UTC, datetime, timedelta

from features.zevent.backoff import RetryGate

T0 = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)


def test_first_attempt_is_allowed() -> None:
    assert RetryGate(timedelta(minutes=10)).ready(T0)


def test_success_holds_off_for_the_full_interval() -> None:
    gate = RetryGate(timedelta(minutes=10))
    gate.succeeded(T0)

    assert not gate.ready(T0 + timedelta(minutes=9))
    assert gate.ready(T0 + timedelta(minutes=10))
    assert gate.failures == 0


def test_failure_backs_off_instead_of_retrying_every_cycle() -> None:
    """The bug this guards: an un-stamped failure retried on every 30 s loop."""
    gate = RetryGate(timedelta(hours=6), retry_delay=timedelta(minutes=1))
    gate.failed(T0)

    # Not immediately, and not on the next refresh cycle either.
    assert not gate.ready(T0 + timedelta(seconds=30))
    assert gate.ready(T0 + timedelta(minutes=1))


def test_consecutive_failures_double_the_delay_up_to_the_cap() -> None:
    gate = RetryGate(
        timedelta(hours=6),
        retry_delay=timedelta(minutes=1),
        max_retry_delay=timedelta(minutes=30),
    )
    for expected in (1, 2, 4, 8, 16, 30, 30):
        gate.failed(T0)
        assert not gate.ready(T0 + timedelta(minutes=expected) - timedelta(seconds=1))
        assert gate.ready(T0 + timedelta(minutes=expected))


def test_a_success_clears_the_backoff() -> None:
    gate = RetryGate(timedelta(minutes=10), retry_delay=timedelta(minutes=1))
    for _ in range(5):
        gate.failed(T0)
    assert gate.failures == 5

    gate.succeeded(T0)
    assert gate.failures == 0
    assert gate.ready(T0 + timedelta(minutes=10))

    # And the next failure restarts from the short delay, not the wide one.
    gate.failed(T0)
    assert gate.ready(T0 + timedelta(minutes=1))


def test_an_outage_costs_far_fewer_requests_than_the_refresh_loop() -> None:
    """Count attempts over an hour of total outage, at a 30 s refresh."""
    gate = RetryGate(timedelta(hours=6), retry_delay=timedelta(minutes=1))
    attempts = 0
    for cycle in range(120):  # one hour of 30 s cycles
        now = T0 + timedelta(seconds=30 * cycle)
        if gate.ready(now):
            attempts += 1
            gate.failed(now)

    assert attempts <= 8, f"{attempts} requests/hour is too many for a community server"
