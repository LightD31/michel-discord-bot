"""Cadence of the low-vote reminder on the daily Spotify poll."""

import pytest

from features.spotify.vote_reminder import should_send_low_vote_reminder


@pytest.mark.parametrize("every_days", [0, -1])
def test_disabled_never_reminds(every_days):
    assert not any(should_send_low_vote_reminder(n, every_days) for n in range(10))


def test_daily_reminds_on_every_extension():
    assert [should_send_low_vote_reminder(n, 1) for n in range(1, 4)] == [True, True, True]


def test_every_n_days_reminds_on_multiples_only():
    reminded = [n for n in range(1, 10) if should_send_low_vote_reminder(n, 3)]
    assert reminded == [3, 6, 9]


def test_no_reminder_before_first_extension():
    assert not should_send_low_vote_reminder(0, 1)
