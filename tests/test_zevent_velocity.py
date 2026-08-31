"""Unit tests for ``features.zevent.velocity`` and the ETA-driven boost.

The scenario that matters is a community piling onto one streamer's goal: the
rate spikes, the ETA collapses, and that goal should surface *while* it is
happening rather than after.
"""

from datetime import UTC, datetime, timedelta

from features.zevent.models import DonationGoal, Participant
from features.zevent.stats import (
    DEFAULT_VELOCITY_WEIGHT,
    goal_score,
    velocity_bonus,
)
from features.zevent.velocity import DonationVelocity

T0 = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


def _cycles(v: DonationVelocity, series: list[tuple[str, float]], step_s: int = 30) -> None:
    """Feed observations at a fixed cadence, mimicking the refresh loop."""
    for i, (login, amount) in enumerate(series):
        v.record({login: amount}, T0 + timedelta(seconds=step_s * i))


def test_a_single_sample_yields_no_rate() -> None:
    v = DonationVelocity()
    v.record({"alice": 100.0}, T0)
    assert v.rate_per_minute("alice") is None
    assert v.eta_minutes("alice", 50.0) is None


def test_steady_donations_give_a_steady_rate() -> None:
    v = DonationVelocity()
    # +50 € every 30 s == 100 €/min
    _cycles(v, [("alice", 50.0 * i) for i in range(1, 6)])
    assert v.rate_per_minute("alice") == 100.0


def test_a_flat_total_reads_as_zero_not_unknown() -> None:
    v = DonationVelocity()
    _cycles(v, [("alice", 500.0)] * 4)
    assert v.rate_per_minute("alice") == 0.0
    # Zero rate means an unbounded wait, not an imminent one.
    assert v.eta_minutes("alice", 100.0) is None


def test_eta_shrinks_as_a_community_piles_on() -> None:
    """A raid: the same goal goes from hours away to minutes away."""
    calm = DonationVelocity()
    _cycles(calm, [("alice", 10.0 * i) for i in range(1, 6)])  # 20 €/min
    slow_eta = calm.eta_minutes("alice", 6_000.0)
    assert slow_eta is not None and slow_eta > 120  # hours out

    raid = DonationVelocity()
    _cycles(raid, [("alice", 1_000.0 * i) for i in range(1, 6)])  # 2 000 €/min
    fast_eta = raid.eta_minutes("alice", 6_000.0)
    assert fast_eta is not None and fast_eta < 5  # minutes out

    assert velocity_bonus(fast_eta) > velocity_bonus(slow_eta)


def test_an_already_reached_goal_is_immediate() -> None:
    v = DonationVelocity()
    _cycles(v, [("alice", 100.0 * i) for i in range(1, 4)])
    assert v.eta_minutes("alice", 0.0) == 0.0


def test_the_window_forgets_an_old_burst() -> None:
    """A surge that ended ten minutes ago must not still read as urgent."""
    v = DonationVelocity(window=timedelta(minutes=5))
    v.record({"alice": 0.0}, T0)
    v.record({"alice": 100_000.0}, T0 + timedelta(minutes=1))  # the burst
    # Ten quiet minutes at the same total.
    for i in range(2, 12):
        v.record({"alice": 100_000.0}, T0 + timedelta(minutes=i))

    assert v.rate_per_minute("alice") == 0.0


def test_a_total_going_backwards_restarts_rather_than_inventing_a_rate() -> None:
    v = DonationVelocity()
    v.record({"alice": 5_000.0}, T0)
    v.record({"alice": 6_000.0}, T0 + timedelta(seconds=30))
    # Source reset / partial payload.
    v.record({"alice": 0.0}, T0 + timedelta(seconds=60))
    assert v.rate_per_minute("alice") is None

    v.record({"alice": 100.0}, T0 + timedelta(seconds=90))
    assert v.rate_per_minute("alice") == 200.0


def test_absent_streamers_are_dropped() -> None:
    v = DonationVelocity()
    v.record({"alice": 1.0, "bob": 1.0}, T0)
    assert v.tracked() == 2
    v.record({"alice": 2.0}, T0 + timedelta(seconds=30))
    assert v.tracked() == 1
    assert v.rate_per_minute("bob") is None


# ── the boost curve ──────────────────────────────────────────────────


def test_bonus_decays_with_eta_and_is_zero_when_unknown() -> None:
    assert velocity_bonus(None) == 0.0
    assert velocity_bonus(0.0) == DEFAULT_VELOCITY_WEIGHT
    assert velocity_bonus(1.0) < velocity_bonus(0.0)
    assert velocity_bonus(600.0) < velocity_bonus(60.0)
    # Far-off goals are left essentially untouched.
    assert velocity_bonus(10_000.0) < 0.01


def test_zero_weight_disables_the_bonus() -> None:
    assert velocity_bonus(0.0, velocity_weight=0.0) == 0.0
    assert velocity_bonus(2.0, velocity_weight=0.0) == 0.0


def test_negative_weight_is_clamped_not_inverted() -> None:
    assert velocity_bonus(1.0, velocity_weight=-5.0) == 0.0


def _participant(login: str, raised: float, goal: float, live: bool = True) -> Participant:  # noqa: FBT002
    return Participant(
        display_name=login,
        twitch_login=login,
        twitch_id=login,
        location="LAN",
        raw_location="lan",
        live=live,
        amount_raised=raised,
        next_goal=DonationGoal(name=f"{login} goal", amount=goal),
    )


def test_the_bonus_is_additive_so_it_lifts_by_an_absolute_amount() -> None:
    """A multiplicative boost would scale with size and defeat the purpose.

    The base score is logarithmic in euros, so multiplying lifts a big
    streamer by whole points and a mid-tier one by a fraction — the opposite
    of surfacing a raid. Adding gives both the same absolute lift.
    """
    small = _participant("small", raised=8_000, goal=10_000)
    big = _participant("big", raised=800_000, goal=1_000_000)
    etas = {"small": 0.0, "big": 0.0}

    lift_small = goal_score(small, etas=etas) - goal_score(small)
    lift_big = goal_score(big, etas=etas) - goal_score(big)
    assert lift_small == lift_big == DEFAULT_VELOCITY_WEIGHT


def test_a_raid_lifts_a_mid_tier_goal_above_a_much_bigger_quiet_one() -> None:
    mid = _participant("mid", raised=8_000, goal=10_000)
    big = _participant("big", raised=85_000, goal=90_000)

    assert goal_score(mid) < goal_score(big)
    assert goal_score(mid, etas={"mid": 1.0}) > goal_score(big)


def test_a_raid_cannot_lift_a_channel_that_raised_almost_nothing() -> None:
    """The bonus is finite: it surfaces a real goal, not any goal."""
    tiny = _participant("tiny", raised=1, goal=5)
    big = _participant("big", raised=85_000, goal=90_000)

    assert goal_score(tiny, etas={"tiny": 0.0}) < goal_score(big)
