"""Tests for the free ``/im premium`` of a ZUnivers Premium subscription.

A subscription grants 3 of them, 10 days apart. The API reports the subscription
on ``/user/{username}`` and each invocation as 10 ``INVOKE`` entries of the
``premium`` event (one per card) in ``/inventory/{username}/logs``.
"""

from datetime import datetime

from features.coloc import PARIS_TZ, PremiumStatus, premium_invocation_dates
from features.coloc.constants import (
    PREMIUM_HINT,
    PREMIUM_REMAINING_HINT,
    PREMIUM_REMINDERS,
    format_premium_link,
)

LINK = "https://discord.com/channels/138283154589876224/808432657838768168"
SUBSCRIPTION = {"beginDate": "2026-10-11T23:55:54.497696", "endDate": "2026-11-10T23:55:54.497696"}


def _user(subscription: dict | None = SUBSCRIPTION) -> dict:
    return {"user": {"discordUserName": "someone"}, "subscription": subscription}


def _logs(*timestamps: str, event: str = "premium", source: str = "INVOKE") -> dict:
    """Build a logs page: 10 cards per invocation, grouped by their shared timestamp."""
    return {
        timestamp: [{"date": timestamp, "source": source, "eventName": event}] * 10
        for timestamp in timestamps
    }


def _paris(*args: int) -> datetime:
    return PARIS_TZ.localize(datetime(*args))


def test_invocation_dates_count_one_per_timestamp_oldest_first():
    logs = _logs("2026-10-21T08:00:00.000000", "2026-10-11T23:57:50.627709")
    assert premium_invocation_dates(logs) == [
        _paris(2026, 10, 11, 23, 57, 50, 627709),
        _paris(2026, 10, 21, 8, 0),
    ]


def test_invocation_dates_ignore_other_invocations_and_sources():
    logs = {
        **_logs("2026-10-12T08:00:00.000000", event=""),
        **_logs("2026-10-13T08:00:00.000000", event="zevent2026"),
        **_logs("2026-10-14T08:00:00.000000", source="RECYCLE"),
    }
    assert premium_invocation_dates(logs) == []


def test_not_subscribed_has_no_status():
    assert PremiumStatus.from_api(_user(None), {}) is None
    assert PremiumStatus.from_api({}, {}) is None


def test_fresh_subscription_has_one_available_right_away():
    status = PremiumStatus.from_api(_user(), {})
    assert status is not None
    assert status.remaining == 3
    assert status.is_available(_paris(2026, 10, 12, 8, 0))


def test_cooldown_of_ten_days_after_an_invocation():
    status = PremiumStatus.from_api(_user(), _logs("2026-10-11T23:57:50.627709"))
    assert status is not None
    assert status.remaining == 2
    assert status.next_available == _paris(2026, 10, 21, 23, 57, 50, 627709)
    assert not status.is_available(_paris(2026, 10, 21, 20, 0))
    assert status.is_available(_paris(2026, 10, 22, 20, 0))


def test_invocations_before_the_subscription_dont_count():
    logs = _logs("2026-10-11T23:57:50.627709", "2022-04-02T13:22:03.613927")
    status = PremiumStatus.from_api(_user(), logs)
    assert status is not None
    assert status.invocations == [_paris(2026, 10, 11, 23, 57, 50, 627709)]
    assert status.remaining == 2


def test_none_left_after_three_invocations():
    logs = _logs(
        "2026-10-11T23:57:50.627709", "2026-10-22T08:00:00.000000", "2026-11-01T08:00:00.000000"
    )
    status = PremiumStatus.from_api(_user(), logs)
    assert status is not None
    assert status.remaining == 0
    assert status.next_available is None
    assert not status.is_available(_paris(2026, 11, 10, 8, 0))


def test_not_available_once_the_subscription_ended():
    status = PremiumStatus.from_api(_user(), {})
    assert status is not None
    assert not status.is_available(_paris(2026, 11, 11, 8, 0))


def test_format_premium_link():
    assert format_premium_link(LINK) == f"[/im premium]({LINK})"
    assert format_premium_link(None) == "`/im premium`"


def test_premium_templates_render_with_the_link():
    for template in (*PREMIUM_REMINDERS, PREMIUM_HINT):
        rendered = template.format(im=format_premium_link(LINK))
        assert LINK in rendered
        assert "{" not in rendered
    assert PREMIUM_REMAINING_HINT.format(remaining=2, limit=3) == (
        "(2/3 restants sur ton abonnement)"
    )
