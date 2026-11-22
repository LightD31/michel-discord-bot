"""The Speedons schedule (extensions/speedons.py), rendered from the API's payloads.

``fetch_untrusted`` answers the four endpoints below (the shape of the 2025
edition's API): the schedule pages, the run in progress and the page to land
on are checked without the network.
"""

from datetime import timedelta

import pytest

from extensions import speedons
from extensions.speedons import render_schedule
from src.core import clock

BASE = "https://api.example.org/campaigns/speedons-4"


def event(eid, start_hours, *, game="Celeste", participants=(), **props):
    now = clock.now()
    return {
        "id": eid,
        "start_date": (now + timedelta(hours=start_hours)).isoformat(),
        "end_date": (now + timedelta(hours=start_hours + 1)).isoformat(),
        "participants": [{"attendee_id": a, "role": r} for a, r in participants],
        "properties": {"game": game, **props},
    }


def payloads(events):
    return {
        f"{BASE}/events": events,
        f"{BASE}/attendees": [{"id": 1, "name": "Léa"}, {"id": 2, "name": "Hugo"}],
        BASE: {"amount": "1234.5"},
        f"{BASE}/challenges": [{"event_id": "e2", "name": "Any% sans dash"}],
    }


@pytest.fixture
def api(monkeypatch):
    answers: dict = {}

    async def fetch_untrusted(url, return_type):
        if url not in answers:
            raise ConnectionError("down")
        return {"data": answers[url]}

    monkeypatch.setattr(speedons, "fetch_untrusted", fetch_untrusted)
    return answers


async def test_the_schedule_names_runners_commentators_and_incentives(api):
    api.update(
        payloads(
            [
                event("e1", -3, participants=[(1, "ACTOR")], category="Any%", estimate="1:00"),
                event(
                    "e2",
                    -0.5,  # in progress
                    game="Hollow Knight",
                    participants=[(1, "ACTOR"), (2, "ACTOR"), (2, "COMMENTATOR")],
                ),
                event("e3", 2, participants=[(9, "ACTOR")]),  # an unknown attendee
            ]
        )
    )

    pages, live, page_index = await render_schedule(BASE, None)

    (page,) = pages
    assert page.title == "Speedons 4 (1234.50€)"
    first, current, last = page.fields
    assert "Runner : **Léa**" in first.value
    assert "Catégorie : **Any%** - Estimé : **1:00**" in first.value
    assert "Hollow Knight" in current.name and current.name.startswith("<:zrtON:")
    assert "Runners : **Léa, Hugo**" in current.value
    assert "Commentateur : **Hugo**" in current.value
    assert "Incentive : **Any% sans dash**" in current.value
    assert "Runner : **Unknown**" in last.value
    assert live.title == "Run en cours (1234.50€)"
    assert live.fields[0].name == "Hollow Knight"
    assert page_index == 0


async def test_the_schedule_lands_on_the_page_of_the_run_in_progress(api):
    events = [event(f"e{i}", i - 6) for i in range(12)]  # e5 runs now, on page 2
    api.update(payloads(events))

    pages, live, page_index = await render_schedule(BASE, "https://cdn.example.org/i.png")

    assert [len(p.fields) for p in pages] == [5, 5, 2]
    assert page_index == 1
    assert pages[0].thumbnail.url == "https://cdn.example.org/i.png"


async def test_without_a_run_in_progress_the_live_embed_says_so(api):
    api.update(payloads([event("e1", 5)]))
    _, live, page_index = await render_schedule(BASE, None)
    assert live.title == "Pas de run en cours (1234.50€)" and page_index == 0


async def test_an_endpoint_down_skips_the_refresh(api):
    answers = payloads([event("e1", 5)])
    del answers[f"{BASE}/challenges"]
    api.update(answers)
    assert await render_schedule(BASE, None) is None
