"""Unit tests for ``features.zevent.discord_event``.

The plan drives a real Discord scheduled event, so the phase rules and the
rendering of a live donation total are what matter here.
"""

from datetime import UTC, datetime, timedelta

from features.zevent.discord_event import (
    ACTIVE,
    COMPLETED,
    FALLBACK_DURATION,
    MAX_DESCRIPTION,
    MAX_NAME,
    SCHEDULED,
    amount_line,
    plan_scheduled_event,
    resolve_end,
)

EVENT_START = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
MAIN_START = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
EVENT_END = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)


def _plan(now: datetime, **kwargs):
    params = {
        "title": "ZEvent 2026",
        "event_start": EVENT_START,
        "main_event_start": MAIN_START,
        "event_end": EVENT_END,
        "now": now,
    }
    params.update(kwargs)
    return plan_scheduled_event(**params)


# ─── End resolution ───────────────────────────────────────────────────


def test_resolve_end_prefers_the_published_end() -> None:
    assert resolve_end(EVENT_END, EVENT_START, MAIN_START) == EVENT_END


def test_resolve_end_falls_back_when_the_api_publishes_none() -> None:
    assert resolve_end(None, EVENT_START, MAIN_START) == MAIN_START + FALLBACK_DURATION


def test_resolve_end_ignores_an_end_before_the_start() -> None:
    """Discord rejects such a window, so an unusable end counts as missing."""
    stale = EVENT_START - timedelta(days=1)
    assert resolve_end(stale, EVENT_START, MAIN_START) == MAIN_START + FALLBACK_DURATION


# ─── Phases ───────────────────────────────────────────────────────────


def test_before_the_start_the_event_is_only_announced() -> None:
    plan = _plan(EVENT_START - timedelta(days=2))
    assert plan.status == SCHEDULED
    assert plan.start == EVENT_START
    assert plan.end == EVENT_END
    assert "concert d'ouverture" in plan.description.lower()


def test_concert_phase_is_flagged_as_live() -> None:
    plan = _plan(EVENT_START + timedelta(hours=1), concert_active=True)
    assert plan.status == ACTIVE
    assert "en direct" in plan.description


def test_concert_window_without_a_live_channel_stays_neutral() -> None:
    plan = _plan(EVENT_START + timedelta(hours=1), concert_active=False)
    assert plan.status == ACTIVE
    assert "en direct" not in plan.description


def test_marathon_phase_announces_the_marathon() -> None:
    plan = _plan(MAIN_START + timedelta(hours=5))
    assert plan.status == ACTIVE
    assert "Marathon caritatif en cours" in plan.description


def test_past_the_end_the_event_is_completed() -> None:
    assert _plan(EVENT_END + timedelta(minutes=1)).status == COMPLETED


def test_finished_forces_completion_mid_event() -> None:
    """``/zevent_finish`` closes the event even before its scheduled end."""
    plan = _plan(MAIN_START + timedelta(hours=5), finished=True)
    assert plan.status == COMPLETED


# ─── Description ──────────────────────────────────────────────────────


def test_the_live_total_is_rendered_as_is() -> None:
    """Editing an event notifies nobody, so the figure need not be rounded."""
    plan = _plan(MAIN_START + timedelta(hours=5), total=1_284_990)
    assert "1 284 990 € récoltés." in plan.description


def test_the_description_follows_the_total_as_it_moves() -> None:
    now = MAIN_START + timedelta(hours=5)
    first = _plan(now, total=1_284_990)
    second = _plan(now, total=1_285_120)
    assert first.description != second.description


def test_no_amount_line_before_donations_open() -> None:
    plan = _plan(EVENT_START - timedelta(days=2))
    assert "récoltés" not in plan.description


def test_tracker_link_is_included_when_known() -> None:
    plan = _plan(MAIN_START + timedelta(hours=5), tracker_url="https://discord.com/channels/1/2/3")
    assert "[Suivi en direct](https://discord.com/channels/1/2/3)" in plan.description


def test_no_tracker_link_when_the_message_is_missing() -> None:
    assert "Suivi en direct" not in _plan(MAIN_START + timedelta(hours=5)).description


# ─── Amount line ──────────────────────────────────────────────────────


def test_amount_line_is_omitted_when_there_is_nothing_to_announce() -> None:
    assert amount_line(None) is None
    assert amount_line(0.0) is None
    assert amount_line(-5.0) is None


# ─── Discord's own limits ─────────────────────────────────────────────


def test_name_and_description_stay_within_discord_limits() -> None:
    plan = _plan(
        MAIN_START + timedelta(hours=5),
        title="Z" * 500,
        total=1_000_000,
        tracker_url="https://discord.com/channels/1/2/3",
    )
    assert len(plan.name) == MAX_NAME
    assert len(plan.description) <= MAX_DESCRIPTION


def test_an_empty_title_falls_back_to_a_usable_name() -> None:
    assert _plan(EVENT_START - timedelta(days=2), title="").name == "Zevent"
