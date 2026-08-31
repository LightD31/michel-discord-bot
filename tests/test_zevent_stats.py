"""Unit tests for ``features.zevent.stats``.

Payload shapes are copied from the live EvenMoreStats API (2026 edition).
"""

from datetime import UTC, datetime

from features.zevent.stats import (
    LAN,
    ONLINE,
    build_location_index,
    event_schedule,
    location_bucket,
    parse_datetime,
    parse_participants,
    parse_shows,
    resolve_location,
    select_event,
    upcoming_goals,
    upcoming_shows,
)

OVERVIEW_SAMPLE = [
    {
        "id": "019fc7a3-8347-7f70-9f9a-ae2ca8281d20",
        "name": "Alderiate",
        "location": "lan",
        "live": False,
        "amount_raised": 125050,
        "donation_goals_count": 14,
        "next_donation_goal": {"name": "Je compte jusqu'à 1 000", "amount": 200000},
        "socials": {"twitch": {"id": "77452537", "login": "alderiate"}},
    },
    {
        "id": "019fc7a3-8215-70b5-98e4-359831fa7f44",
        "name": "Aducine",
        "location": "remote",
        "live": True,
        "amount_raised": 5000,
        "next_donation_goal": {"name": "Apéro handcam", "amount": 800000},
        "socials": {"twitch": {"id": "44842076", "login": "aducine"}},
    },
    {
        "id": "019fc7a3-967c-7159-85bd-4720c7cc7e3b",
        "name": "Flonflon",
        "location": "remote_villa",
        "live": True,
        "amount_raised": 700,
        "socials": {"twitch": {"id": "123456", "login": "flonflon"}},
    },
    {
        # No Twitch social: unusable as a key, must be skipped.
        "name": "Orchestre Curieux",
        "location": "lan",
        "live": False,
        "amount_raised": 0,
        "socials": {},
    },
]

SHOWS_SAMPLE = [
    {
        "id": "01a00b12-eee6-73ae-abb9-2a006ebdc126",
        "name": "Concert",
        "description": "",
        "schedule": {"start": "2026-09-03T18:00:00Z", "end": "2026-09-03T21:30:00Z"},
        "all_day": False,
        "participants": [
            {
                "streamer_name": "ZEVENT",
                "role": "host",
                "socials": {"twitch": {"id": "77870741", "login": "zevent"}},
            },
            {"streamer_name": "Bigflo et Oli", "role": "guest", "socials": {}},
            {"streamer_name": "GIMS", "role": "guest", "socials": {}},
        ],
    },
    {
        "id": "01a00b2a-0000-7dfc-9d19-10edb89269b2",
        "name": "Rush final",
        "description": "",
        "schedule": {"start": "2026-09-06T18:00:00Z", "end": "2026-09-06T23:00:00Z"},
        "all_day": False,
        "participants": [],
    },
    {
        "id": "01a00b2a-1111-7dfc-9d19-10edb89269b2",
        "name": "Lancement ZEVENT",
        "description": "",
        "schedule": {"start": "2026-09-04T16:00:00Z", "end": "2026-09-04T16:10:00Z"},
        "all_day": False,
        "participants": [{"streamer_name": "Dach", "role": "host", "socials": {}}],
    },
]

EVENTS_SAMPLE = [
    {
        "id": "019d3f95-bd24-7e5d-861b-1de6243e3169",
        "name": "ZEvent 2025",
        "schedule": {"start": "2025-09-04T10:00:00Z", "end": "2025-09-08T02:00:00Z"},
    },
    {
        "id": "019f5bd1-fe07-7d78-a326-a02198a9d50f",
        "name": "ZEvent 2026",
        "schedule": {"start": "2026-09-03T18:00:00Z", "end": "2026-09-07T00:00:00Z"},
    },
    {
        "id": "019d20ef-fb85-7c13-b0dd-21ee8b6b9000",
        "name": "Birds Of Prey #6",
        "schedule": {"start": "2025-11-08T13:00:00Z", "end": "2025-11-09T16:00:00Z"},
    },
]


# ── locations ────────────────────────────────────────────────────────


def test_only_the_venue_counts_as_lan() -> None:
    assert location_bucket("lan") == LAN
    assert location_bucket("remote") == ONLINE
    # Satellite setups are remote, not the LAN.
    assert location_bucket("remote_zbase") == ONLINE
    assert location_bucket("remote_villa") == ONLINE
    assert location_bucket("remote_ankama") == ONLINE
    assert location_bucket(None) == ONLINE
    assert location_bucket("") == ONLINE


def test_parse_participants_buckets_and_converts_centimes() -> None:
    participants = parse_participants(OVERVIEW_SAMPLE)

    # The entry without a Twitch login is dropped.
    assert [p.twitch_login for p in participants] == ["alderiate", "aducine", "flonflon"]

    alderiate = participants[0]
    assert alderiate.location == LAN
    assert alderiate.amount_raised == 1250.50
    assert alderiate.live is False

    assert participants[1].location == ONLINE
    assert participants[2].location == ONLINE
    assert participants[2].amount_raised == 7.0


def test_parse_participants_tolerates_junk() -> None:
    assert parse_participants(None) == []
    assert parse_participants({"data": []}) == []
    assert parse_participants(["nope", 42, {"name": "x"}]) == []


def test_location_index_keys_on_login_and_twitch_id() -> None:
    index = build_location_index(parse_participants(OVERVIEW_SAMPLE))
    assert index["alderiate"] == LAN
    assert index["77452537"] == LAN
    assert index["aducine"] == ONLINE
    assert index["44842076"] == ONLINE


def test_resolve_location_uses_the_index_when_zevent_omits_it() -> None:
    """zevent.fr dropped ``location`` in 2026 — the index has to fill in."""
    index = build_location_index(parse_participants(OVERVIEW_SAMPLE))

    assert resolve_location({"twitch": "Alderiate", "twitch_id": "77452537"}, index) == LAN
    assert resolve_location({"twitch": "aducine", "twitch_id": "44842076"}, index) == ONLINE
    # Login missing on the zevent.fr side: the Twitch id still resolves it.
    assert resolve_location({"twitch": "", "twitch_id": "77452537"}, index) == LAN
    # Unknown streamer falls back to the far larger remote group.
    assert resolve_location({"twitch": "someoneelse"}, index) == ONLINE


def test_resolve_location_prefers_a_location_zevent_does_send() -> None:
    """If zevent.fr brings the field back, it wins over the index."""
    index = {"alderiate": ONLINE}
    assert resolve_location({"twitch": "alderiate", "location": "LAN"}, index) == LAN
    assert resolve_location({"twitch": "alderiate", "location": "lan"}, index) == LAN
    assert resolve_location({"twitch": "alderiate", "location": "Online"}, index) == ONLINE


# ── shows ────────────────────────────────────────────────────────────


def test_parse_shows_sorts_by_start_and_splits_roles() -> None:
    shows = parse_shows(SHOWS_SAMPLE)

    assert [s.name for s in shows] == ["Concert", "Lancement ZEVENT", "Rush final"]

    concert = shows[0]
    assert concert.start == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert concert.end == datetime(2026, 9, 3, 21, 30, tzinfo=UTC)
    assert concert.hosts == ["ZEVENT"]
    assert concert.guests == ["Bigflo et Oli", "GIMS"]
    assert shows[2].hosts == [] and shows[2].guests == []


def test_parse_shows_tolerates_junk() -> None:
    assert parse_shows(None) == []
    assert parse_shows({"data": []}) == []
    unnamed = parse_shows([{"schedule": {"start": "nonsense"}}])
    assert len(unnamed) == 1
    assert unnamed[0].name == "Événement"
    assert unnamed[0].start is None


def test_upcoming_shows_drops_finished_entries() -> None:
    shows = parse_shows(SHOWS_SAMPLE)
    during = datetime(2026, 9, 4, 17, 0, tzinfo=UTC)

    pending = upcoming_shows(shows, during)
    assert [s.name for s in pending] == ["Rush final"]

    assert len(upcoming_shows(shows, datetime(2026, 9, 1, tzinfo=UTC), limit=2)) == 2
    assert upcoming_shows(shows, datetime(2027, 1, 1, tzinfo=UTC)) == []


# ── event selection ──────────────────────────────────────────────────


def test_select_event_prefers_the_running_edition() -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now)
    assert event is not None
    assert event["name"] == "ZEvent 2026"


def test_select_event_falls_back_to_the_next_one_to_start() -> None:
    now = datetime(2025, 9, 20, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now)
    assert event is not None
    # Birds Of Prey (November) starts before ZEvent 2026.
    assert event["name"] == "Birds Of Prey #6"


def test_select_event_falls_back_to_the_most_recent_past_edition() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now)
    assert event is not None
    assert event["name"] == "ZEvent 2026"


def test_select_event_honours_an_explicit_id() -> None:
    now = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    event = select_event(EVENTS_SAMPLE, now, "019d3f95-bd24-7e5d-861b-1de6243e3169")
    assert event is not None
    assert event["name"] == "ZEvent 2025"

    assert select_event(EVENTS_SAMPLE, now, "does-not-exist") is None


def test_select_event_tolerates_junk() -> None:
    now = datetime(2026, 9, 5, tzinfo=UTC)
    assert select_event(None, now) is None
    assert select_event([], now) is None
    assert select_event(["nope"], now) is None


# ── timestamps ───────────────────────────────────────────────────────


def test_parse_datetime_handles_z_suffix_and_naive_values() -> None:
    assert parse_datetime("2026-09-03T18:00:00Z") == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert parse_datetime("2026-09-03T18:00:00+02:00").utcoffset().total_seconds() == 7200
    # A naive timestamp is assumed UTC rather than crashing the comparison.
    assert parse_datetime("2026-09-03T18:00:00") == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert parse_datetime("") is None
    assert parse_datetime(None) is None
    assert parse_datetime("not a date") is None


# ── donation goals ───────────────────────────────────────────────────


def test_parse_participants_reads_the_next_goal() -> None:
    by_login = {p.twitch_login: p for p in parse_participants(OVERVIEW_SAMPLE)}

    alderiate = by_login["alderiate"]
    assert alderiate.next_goal is not None
    assert alderiate.next_goal.name == "Je compte jusqu'à 1 000"
    assert alderiate.next_goal.amount == 2000.0  # centimes -> euros

    # Flonflon's fixture entry has no goal at all.
    assert by_login["flonflon"].next_goal is None


def test_parse_goal_rejects_unusable_entries() -> None:
    def one(goal: object) -> object:
        payload = [
            {
                "name": "X",
                "location": "lan",
                "amount_raised": 0,
                "next_donation_goal": goal,
                "socials": {"twitch": {"id": "1", "login": "x"}},
            }
        ]
        return parse_participants(payload)[0].next_goal

    assert one(None) is None
    assert one("nope") is None
    assert one({"name": "no amount"}) is None
    assert one({"amount": 100}) is None  # no name
    assert one({"name": "   ", "amount": 100}) is None


def test_upcoming_goals_puts_live_streamers_first_then_closest() -> None:
    participants = parse_participants(OVERVIEW_SAMPLE)
    ranked = upcoming_goals(participants)

    # Aducine is live (gap 8000 - 50 = 7950); Alderiate is offline with a
    # smaller gap (2000 - 1250.50 = 749.50) but still sorts after.
    assert [p.twitch_login for p in ranked] == ["aducine", "alderiate"]
    assert upcoming_goals(participants, limit=1)[0].twitch_login == "aducine"


def test_upcoming_goals_is_empty_without_goals() -> None:
    assert upcoming_goals([]) == []
    no_goals = [p for p in parse_participants(OVERVIEW_SAMPLE) if p.next_goal is None]
    assert upcoming_goals(no_goals) == []


# ── event schedule ───────────────────────────────────────────────────


def test_event_schedule_splits_open_and_fundraising_starts() -> None:
    event = {
        "schedule": {"start": "2026-09-03T18:00:00Z", "end": "2026-09-07T00:00:00Z"},
        "schedule_raising": {"start": "2026-09-04T16:00:00Z", "end": "2026-09-07T00:00:00Z"},
    }
    start, raising = event_schedule(event)
    assert start == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert raising == datetime(2026, 9, 4, 16, 0, tzinfo=UTC)


def test_event_schedule_tolerates_missing_blocks() -> None:
    assert event_schedule(None) == (None, None)
    assert event_schedule({}) == (None, None)
    assert event_schedule({"schedule": "nope"}) == (None, None)
    start, raising = event_schedule({"schedule": {"start": "2026-09-03T18:00:00Z"}})
    assert start == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
    assert raising is None


def test_upcoming_goals_ranking_is_deterministic_on_ties() -> None:
    """Equal gaps must not let payload order churn the rendered embed."""

    def entry(name: str, login: str) -> dict:
        return {
            "name": name,
            "location": "remote",
            "live": False,
            "amount_raised": 0,
            "next_donation_goal": {"name": "same goal", "amount": 100},
            "socials": {"twitch": {"id": login, "login": login}},
        }

    forward = parse_participants([entry("Zoe", "zoe"), entry("alice", "alice")])
    backward = parse_participants([entry("alice", "alice"), entry("Zoe", "zoe")])

    assert [p.display_name for p in upcoming_goals(forward)] == ["alice", "Zoe"]
    assert [p.display_name for p in upcoming_goals(backward)] == ["alice", "Zoe"]
