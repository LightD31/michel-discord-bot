"""Tests for ``features.zerator247`` — the ZeratoR 24/7 vote cycle and !request reminders.

Payload shape mirrors ``GET https://247.zerator.com/api/now``.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiohttp import ClientConnectionError

from features.zerator247 import (
    Cycle,
    RequestSubscription,
    Zerator247Client,
    Zerator247Error,
    build_request_reminder,
    describe_cycle,
    should_notify,
)

AT_MS = 1_791_839_200_000
AT = datetime.fromtimestamp(AT_MS / 1000, UTC)


def _payload(phase: str = "requests", remaining: float | None = 900.0, **extra: Any) -> dict:
    return {
        "at": AT_MS,
        "stale": False,
        "viewers": 728,
        "cycle": {
            "phase": phase,
            "remainingSeconds": remaining,
            "requestCount": 0,
            "voteSource": "poll",
            "windows": {
                "requestOpensBeforeSeconds": 1200,
                "ballotOpensBeforeSeconds": 600,
                "ballotClosesBeforeSeconds": 300,
            },
        },
        "mainLive": None,
        "next": None,
        **extra,
    }


def _cycle(phase: str = "requests", remaining: float | None = 900.0, **extra: Any) -> Cycle:
    cycle = Cycle.from_payload(_payload(phase, remaining, **extra))
    assert cycle is not None
    return cycle


# ── parsing ──────────────────────────────────────────────────────────


def test_from_payload_computes_the_cycle_windows():
    cycle = _cycle(remaining=900.0)
    assert cycle.phase == "requests"
    assert cycle.ends_at == AT + timedelta(seconds=900)
    assert cycle.requests_open_at == AT - timedelta(seconds=300)
    assert cycle.ballot_opens_at == AT + timedelta(seconds=300)
    assert cycle.ballot_closes_at == AT + timedelta(seconds=600)


@pytest.mark.parametrize("data", [None, [], {}, {"cycle": None}, {"cycle": {"remaining": 3}}])
def test_from_payload_without_a_cycle(data: Any):
    assert Cycle.from_payload(data) is None


def test_from_payload_without_remaining_time_has_no_end():
    cycle = _cycle(remaining=None)
    assert cycle.ends_at is None
    assert cycle.ballot_opens_at is None


# ── should_notify ────────────────────────────────────────────────────


def test_notifies_when_the_requests_window_opens():
    assert should_notify(_cycle("requests"), None)


@pytest.mark.parametrize("phase", ["idle", "ballot", "closed", "due"])
def test_does_not_notify_outside_the_requests_window(phase: str):
    assert not should_notify(_cycle(phase), None)


def test_does_not_notify_twice_for_the_same_cycle():
    first = _cycle(remaining=1150.0)
    # A minute later, the end time computed from the next poll drifts by a few seconds.
    later = Cycle.from_payload({**_payload(remaining=1093.0), "at": AT_MS + 60_000})
    assert later is not None
    assert not should_notify(later, first.ends_at)


def test_notifies_again_for_the_next_cycle():
    previous_end = AT - timedelta(hours=2)
    assert should_notify(_cycle(), previous_end)


def test_does_not_notify_on_stale_data_or_unknown_end():
    assert not should_notify(_cycle(stale=True), None)
    assert not should_notify(_cycle(remaining=None), None)
    assert not should_notify(None, None)


# ── subscriptions ────────────────────────────────────────────────────


def test_subscription_without_hours_accepts_every_hour():
    sub = RequestSubscription("1")
    assert all(sub.accepts(h) for h in range(24))
    assert sub.describe_hours() == "à toute heure"
    assert all(RequestSubscription("1", 8, 8).accepts(h) for h in range(24))


def test_subscription_within_the_day():
    sub = RequestSubscription("1", 9, 23)
    assert sub.accepts(9)
    assert sub.accepts(22)
    assert not sub.accepts(23)
    assert not sub.accepts(3)
    assert sub.describe_hours() == "entre 9h et 23h"


def test_subscription_wrapping_past_midnight():
    sub = RequestSubscription("1", 20, 2)
    assert sub.accepts(20)
    assert sub.accepts(0)
    assert sub.accepts(1)
    assert not sub.accepts(2)
    assert not sub.accepts(12)


# ── messages ─────────────────────────────────────────────────────────


def test_reminder_names_the_command_the_deadline_and_the_links():
    cycle = _cycle(remaining=900.0)
    deadline = int((AT + timedelta(seconds=300)).timestamp())
    message = build_request_reminder(
        cycle,
        twitch_url="https://twitch.tv/zerator_247",
        votes_url="https://247.zerator.com/#votes",
    )
    assert "`!request random`" in message
    assert "`!request <ID>` avec l'ID de la VOD de ton choix" in message
    assert "https://twitch.tv/zerator_247" in message
    assert f"<t:{deadline}:R>" in message
    assert "https://247.zerator.com/#votes" in message


def test_reminder_without_links():
    message = build_request_reminder(_cycle(), twitch_url=None, votes_url=None)
    assert "ZeratoR 24/7" in message
    assert "http" not in message


def test_describe_cycle_per_phase():
    opens = int((AT + timedelta(seconds=2000 - 1200)).timestamp())
    assert f"<t:{opens}:R>" in describe_cycle(_cycle("idle", remaining=2000.0))
    assert "requêtes sont ouvertes" in describe_cycle(_cycle("requests"))
    assert "vote est en cours" in describe_cycle(_cycle("ballot", remaining=500.0))
    assert "prochaine VOD est choisie" in describe_cycle(_cycle("due", remaining=100.0))
    assert "Aucune VOD" in describe_cycle(None)


# ── client ───────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int, body: Any = None):
        self.status = status
        self._body = body

    async def json(self, content_type: str | None = "application/json") -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeSession:
    """Answers each GET with the next queued response (the last one repeats)."""

    def __init__(self, *responses: _FakeResponse | Exception):
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.urls.append(url)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def _client(session: _FakeSession) -> Zerator247Client:
    async def factory() -> Any:
        return session

    return Zerator247Client("https://247.zerator.com/", session_factory=factory)


async def test_client_reads_the_cycle():
    session = _FakeSession(_FakeResponse(200, _payload()))
    cycle = await _client(session).get_cycle()
    assert session.urls == ["https://247.zerator.com/api/now"]
    assert cycle is not None and cycle.phase == "requests"


@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(503, {"message": "down"}),
        _FakeResponse(200, ValueError("not json")),
        ClientConnectionError("refused"),
    ],
)
async def test_client_errors(response: Any):
    with pytest.raises(Zerator247Error):
        await _client(_FakeSession(response)).get_cycle()
