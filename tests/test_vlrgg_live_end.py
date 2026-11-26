"""The end of a live VLR.gg match: no result is published without a « final » page.

``/v2/match?q=live_score`` listed Mandatory vs MIR « LIVE » an hour before its
kick-off, then dropped it: the bot posted « TERMINÉ: ? - ? » with empty stats,
then a second live message at the real start. The match page settles it.
"""

from typing import Any

import pytest
from discord_fakes import FakeMessage

from extensions.vlrgg import VlrggExtension
from extensions.vlrgg import notifications as vlrgg_notifications
from extensions.vlrgg._common import (
    FINALIZE_GIVE_UP_ATTEMPTS,
    FINALIZE_MAX_ATTEMPTS,
    FINALIZE_SLOW_EVERY,
    PendingFinal,
    TeamConfig,
    TeamState,
)
from features.vlrgg import client, not_started

MATCH_ID = "vlrgg_794461"
SEGMENT = {
    "match_id": "794461",
    "team1": "Mandatory",
    "team2": "MIR",
    "score1": "0",
    "score2": "0",
    "match_event": "VCT 2027: EMEA Kickoff - Europe Qualifier Playoffs",
}


def _details(status: str, scores: tuple[str, str] = ("", "")) -> dict[str, Any]:
    return {
        "status": status,
        "teams": [
            {"id": "7967", "name": "Mandatory", "score": scores[0], "is_winner": scores[0] == "2"},
            {"id": "17945", "name": "MIR", "score": scores[1], "is_winner": scores[1] == "2"},
        ],
        "maps": [],
    }


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ext = object.__new__(VlrggExtension)
        self.ext.bot = object()
        self.details: dict[str, Any] = {}
        self.deleted_docs: list[str] = []
        self.edits: list[dict[str, Any]] = []
        self.message = FakeMessage(content="🔴 **Match en direct — Mandatory!**")
        self.team = TeamState(team_config=TeamConfig(name="Mandatory", vlr_team_id="7967"))
        self.team.server_id = "1"
        self.team.ongoing_matches[MATCH_ID] = dict(SEGMENT)
        self.team.live_messages[MATCH_ID] = self.message

        async def live_details(match: dict[str, Any]) -> dict[str, Any]:
            return self.details

        async def delete_doc(server_id: str, match_id: str) -> None:
            self.deleted_docs.append(match_id)

        async def edit(message: Any, **kwargs: Any) -> bool:
            self.edits.append(kwargs)
            return True

        self.ext._live_details = live_details  # type: ignore[method-assign]
        self.ext._delete_live_match = delete_doc  # type: ignore[method-assign]
        monkeypatch.setattr(vlrgg_notifications, "edit_message_if_changed", edit)

    async def end(self) -> None:
        await self.ext._handle_vlr_match_ended(MATCH_ID, self.team)

    async def cycle(self) -> None:
        await self.ext._try_finalize_match(MATCH_ID, self.team)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch)


async def test_a_match_listed_before_its_kickoff_loses_its_live_message(harness) -> None:
    harness.details = _details("58m")

    await harness.end()

    assert harness.message.deleted
    assert harness.edits == []  # no « TERMINÉ: ? - ? »
    assert MATCH_ID not in harness.team.ending_matches
    assert harness.deleted_docs == [MATCH_ID]


async def test_a_match_still_live_on_its_page_is_never_closed_with_a_guess(harness) -> None:
    harness.details = _details("live", ("1", "0"))

    await harness.end()
    for _ in range(FINALIZE_MAX_ATTEMPTS * 3):
        await harness.cycle()

    assert harness.edits == [] and not harness.message.deleted
    assert MATCH_ID in harness.team.ending_matches
    # The stored reference stays, so a restart finds the message again.
    assert harness.deleted_docs == []


async def test_the_final_page_publishes_the_result(harness) -> None:
    harness.details = _details("live", ("1", "0"))
    await harness.end()
    harness.details = _details("final", ("2", "0"))

    await harness.cycle()

    ((edit),) = harness.edits
    assert edit["content"] == "🎉 **Match terminé!** 🎉"
    assert "VICTOIRE" in edit["embeds"][0].title
    assert MATCH_ID not in harness.team.ending_matches
    assert harness.deleted_docs == [MATCH_ID]


async def test_after_the_fast_retries_the_page_is_read_every_few_cycles(harness) -> None:
    reads: list[int] = []

    async def live_details(match: dict[str, Any]) -> dict[str, Any]:
        reads.append(harness.team.ending_matches[MATCH_ID].attempts)
        return _details("live")

    harness.ext._live_details = live_details  # type: ignore[method-assign]
    await harness.end()
    for _ in range(FINALIZE_MAX_ATTEMPTS + 2 * FINALIZE_SLOW_EVERY):
        await harness.cycle()

    slow = [n for n in reads if n > FINALIZE_MAX_ATTEMPTS]
    assert reads[:FINALIZE_MAX_ATTEMPTS] == list(range(1, FINALIZE_MAX_ATTEMPTS + 1))
    assert slow and all(n % FINALIZE_SLOW_EVERY == 0 for n in slow)


async def test_a_match_never_final_is_let_go_without_a_result(harness) -> None:
    harness.details = {}  # page unavailable
    await harness.end()
    harness.team.ending_matches[MATCH_ID].attempts = FINALIZE_GIVE_UP_ATTEMPTS - 1

    await harness.cycle()

    ((edit),) = harness.edits
    assert edit["content"].startswith("⏸️ **Match plus suivi — Mandatory**")
    assert "embeds" not in edit
    assert MATCH_ID not in harness.team.ending_matches
    assert harness.deleted_docs == [MATCH_ID]


async def test_a_match_back_on_the_live_list_keeps_its_message(harness) -> None:
    harness.details = _details("live")
    await harness.end()
    pending = harness.team.ending_matches[MATCH_ID]
    assert isinstance(pending, PendingFinal)

    saved: list[str] = []

    async def save(server_id, team_name, match_id, match_data, channel_id, message_id) -> None:
        saved.append(match_id)

    harness.ext._save_live_match = save  # type: ignore[method-assign]
    harness.message.channel = type("C", (), {"id": 5})()
    await harness.ext._resume_live_match(MATCH_ID, dict(SEGMENT), harness.team)

    assert harness.team.live_messages[MATCH_ID] is harness.message
    assert saved == [MATCH_ID]


# ── Detection ────────────────────────────────────────────────────────────────


def test_a_countdown_on_the_match_page_means_not_started() -> None:
    assert not_started({"status": "58m"})
    assert not_started({"status": "5d 2h"})
    assert not not_started({"status": "LIVE"})
    assert not not_started({"status": "final"})
    assert not not_started({})  # a failed read concludes nothing


async def test_a_match_listed_live_before_its_kickoff_is_not_announced(monkeypatch) -> None:
    team_id = "7967"

    def segments(items: Any) -> dict:
        return {"status": "success", "data": {"status": 200, "segments": items}}

    fixture = {
        "match_id": "794461",
        "url": "https://www.vlr.gg/794461/x",
        "event": "R2 (0-1)",
        "date": "2099/01/01",
        "time": "8:30 pm",
        "team1": {"name": "Mandatory"},
        "team2": {"name": "MIR"},
    }

    async def fake_request(endpoint: str, params: dict[str, str] | None = None) -> dict:
        params = params or {}
        if endpoint == "v2/team":
            q = params.get("q", "")
            if q == "":
                return segments([{"id": team_id, "name": "Mandatory"}])
            if q == "matches":
                return segments([])
            if q == "schedule":
                return segments([fixture])
        if endpoint == "v2/match" and params["q"] == "upcoming":
            return segments([])
        if endpoint == "v2/match" and params["q"] == "live_score":
            return segments([dict(SEGMENT)])
        if endpoint == "v2/match/details":
            details = _details("58m")
            details["teams"][0]["id"] = team_id
            return segments([details])
        raise AssertionError(f"unexpected request {endpoint} {params}")

    client._cache.clear()
    monkeypatch.setattr(client, "vlrgg_request", fake_request)

    data = await client.fetch_all_team_data(team_id, "Mandatory")
    client._cache.clear()

    assert data["live"] == []
    assert [m["match_id"] for m in data["upcoming"]] == ["794461"]
